import asyncio
import json
from typing import Optional, Callable, Any
from fastapi import WebSocket, WebSocketDisconnect
from backend.models import ConnectionStatus, PortInfo, OneBotEvent, LifecycleEvent, MessageEvent
from backend.utils import get_logger
import urllib.parse
import websockets.exceptions

logger = get_logger("websocket_server")


class NapCatConnection:
    def __init__(self, port: int, websocket):
        self.port = port
        self.websocket = websocket
        self.qq_id: Optional[str] = None
        self.qq_name: Optional[str] = None
        self.connected_at = None
        self.last_message_at = None

    async def send_action(self, action: str, params: dict = None, echo: str = None) -> dict:
        payload = {"action": action, "params": params or {}}
        if echo:
            payload["echo"] = echo
        await self.websocket.send_json(payload)
        return payload

    async def send_message(self, group_id: str = None, user_id: str = None, message: list = None):
        params = {"message": message or []}
        if group_id:
            params["group_id"] = group_id
            params["message_type"] = "group"
        elif user_id:
            params["user_id"] = user_id
            params["message_type"] = "private"
        return await self.send_action("send_msg", params)

    async def close(self):
        try:
            await self.websocket.close()
        except Exception:
            pass


class MultiPortWebSocketServer:
    def __init__(self, base_port: int, num_ports: int, host: str = "0.0.0.0"):
        self.base_port = base_port
        self.num_ports = num_ports
        self.host = host
        self.connections: dict[int, Optional[NapCatConnection]] = {}
        self.event_handlers: list[Callable] = []
        self._status_callback: Optional[Callable] = None
        self._servers: dict[int, asyncio.Server] = {}
        self._port_configs: dict[int, dict] = {}

        for i in range(num_ports):
            port = base_port + i
            self.connections[port] = None

    def set_port_configs(self, port_configs: dict[int, dict]):
        """Set port configurations for token validation."""
        self._port_configs = port_configs

    def set_status_callback(self, callback: Callable):
        self._status_callback = callback

    def add_event_handler(self, handler: Callable):
        self.event_handlers.append(handler)

    def get_port_info(self, port: int, name: str = "", character: str = "") -> PortInfo:
        conn = self.connections.get(port)
        if conn:
            return PortInfo(
                port=port,
                name=name,
                character=character,
                qq_id=conn.qq_id,
                qq_name=conn.qq_name,
                status=ConnectionStatus.CONNECTED,
                connected_at=conn.connected_at,
                last_message_at=conn.last_message_at,
            )
        return PortInfo(
            port=port,
            name=name,
            character=character,
            status=ConnectionStatus.DISCONNECTED,
        )

    def get_all_port_info(self, port_configs: dict[int, dict] = None) -> dict[int, PortInfo]:
        result = {}
        for port in self.connections:
            cfg = port_configs.get(port, {}) if port_configs else {}
            result[port] = self.get_port_info(
                port,
                name=cfg.get("name", f"Slot {port - self.base_port + 1}"),
                character=cfg.get("character", ""),
            )
        return result

    async def start_servers(self):
        """Start WebSocket servers on all configured ports."""
        import websockets
        import logging
        
        # Create a logger for websockets that suppresses handshake errors
        ws_logger = logging.getLogger("websockets.server")
        ws_logger.setLevel(logging.CRITICAL)
        
        for i in range(self.num_ports):
            port = self.base_port + i
            try:
                server = await websockets.serve(
                    lambda ws, p=port: self._handle_ws_connection(p, ws),
                    self.host,
                    port,
                    process_request=self._process_request,
                    logger=ws_logger
                )
                self._servers[port] = server
                logger.info(f"WebSocket server started on port {port}")
            except Exception as e:
                logger.error(f"Failed to start WebSocket server on port {port}: {e}")

    async def _process_request(self, path, request_headers):
        """Process request before WebSocket handshake."""
        # Check if this is a WebSocket upgrade request
        if "Upgrade" not in request_headers or request_headers["Upgrade"].lower() != "websocket":
            # Not a WebSocket request, return HTTP response
            return "HTTP/1.1 426 Upgrade Required\r\n\r\n", None
        return None

    async def _handle_ws_connection(self, port: int, websocket):
        """Handle a WebSocket connection from the websockets library."""
        from datetime import datetime
        
        try:
            # Check token if configured
            port_config = self._port_configs.get(port, {})
            required_token = port_config.get("token", "")
            
            if required_token:
                # Extract token from query parameters or headers
                path = websocket.request.path if hasattr(websocket, 'request') else "/"
                query_string = urllib.parse.urlparse(path).query if path else ""
                query_params = urllib.parse.parse_qs(query_string)
                
                # Check query parameter 'access_token'
                token_from_query = query_params.get("access_token", [None])[0]
                
                # Check headers (websockets library provides request headers)
                token_from_header = None
                if hasattr(websocket, 'request') and hasattr(websocket.request, 'headers'):
                    auth_header = websocket.request.headers.get("Authorization", "")
                    if auth_header.startswith("Bearer "):
                        token_from_header = auth_header[7:]
                
                # Validate token
                if token_from_query != required_token and token_from_header != required_token:
                    logger.warning(f"Token validation failed for port {port}")
                    await websocket.close(code=4001, reason="Unauthorized")
                    return
            
            conn = NapCatConnection(port, websocket)
            self.connections[port] = conn
            conn.connected_at = datetime.now()
            
            logger.info(f"New connection on port {port}")
            
            if self._status_callback:
                await self._status_callback("connected", port)
            
            try:
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        conn.last_message_at = datetime.now()
                        await self._process_event(port, data)
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON received on port {port}")
                    except Exception as e:
                        logger.error(f"Error processing message on port {port}: {e}")
            except websockets.exceptions.ConnectionClosed:
                logger.info(f"Client disconnected from port {port}")
            except Exception as e:
                logger.error(f"Error on port {port}: {e}")
            finally:
                self.connections[port] = None
                if self._status_callback:
                    await self._status_callback("disconnected", port)
                    
        except websockets.exceptions.InvalidMessage:
            # Ignore invalid HTTP requests (e.g., TCPing, port scanning)
            logger.debug(f"Invalid HTTP request on port {port} (likely non-WebSocket connection)")
        except Exception as e:
            logger.error(f"Unexpected error on port {port}: {e}")

    async def handle_connection(self, port: int, websocket: WebSocket):
        """Handle a FastAPI WebSocket connection (for backward compatibility)."""
        await websocket.accept()
        conn = NapCatConnection(port, websocket)
        self.connections[port] = conn

        from datetime import datetime
        conn.connected_at = datetime.now()

        logger.info(f"New connection on port {port}")

        if self._status_callback:
            await self._status_callback("connected", port)

        try:
            while True:
                data = await websocket.receive_json()
                conn.last_message_at = datetime.now()
                await self._process_event(port, data)
        except WebSocketDisconnect:
            logger.info(f"Client disconnected from port {port}")
        except Exception as e:
            logger.error(f"Error on port {port}: {e}")
        finally:
            self.connections[port] = None
            if self._status_callback:
                await self._status_callback("disconnected", port)

    async def _process_event(self, port: int, data: dict):
        post_type = data.get("post_type", "")

        if post_type == "meta_event":
            sub_type = data.get("meta_event_type", "")
            if sub_type == "lifecycle":
                self_id = str(data.get("self_id", ""))
                conn = self.connections.get(port)
                if conn and self_id:
                    conn.qq_id = self_id
                    logger.info(f"Port {port} identified as QQ {self_id}")
                    if self._status_callback:
                        await self._status_callback("identified", port, self_id)

        for handler in self.event_handlers:
            try:
                await handler(port, data)
            except Exception as e:
                logger.error(f"Event handler error: {e}")

    async def send_to_port(self, port: int, action: str, params: dict = None) -> Optional[dict]:
        conn = self.connections.get(port)
        if not conn:
            logger.warning(f"No connection on port {port}")
            return None
        return await conn.send_action(action, params)

    async def broadcast(self, action: str, params: dict = None):
        for port, conn in self.connections.items():
            if conn:
                try:
                    await conn.send_action(action, params)
                except Exception as e:
                    logger.error(f"Broadcast error on port {port}: {e}")

    def is_connected(self, port: int) -> bool:
        return self.connections.get(port) is not None

    def get_connection(self, port: int) -> Optional[NapCatConnection]:
        return self.connections.get(port)

    async def stop_servers(self):
        """Stop all WebSocket servers."""
        for port, server in self._servers.items():
            try:
                server.close()
                await server.wait_closed()
                logger.info(f"WebSocket server stopped on port {port}")
            except Exception as e:
                logger.error(f"Error stopping WebSocket server on port {port}: {e}")
        self._servers.clear()
