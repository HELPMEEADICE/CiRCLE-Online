import asyncio
import json
import uuid
from typing import Optional, Callable, Any
from fastapi import WebSocket, WebSocketDisconnect
from backend.models import ConnectionStatus, PortInfo
from backend.utils import get_logger
import urllib.parse
import websockets
from websockets.asyncio.server import ServerConnection

logger = get_logger("websocket_server")


class NapCatConnection:
    def __init__(self, port: int, websocket):
        self.port = port
        self.websocket = websocket
        self.qq_id: Optional[str] = None
        self.qq_name: Optional[str] = None
        self.connected_at = None
        self.last_message_at = None
        self._pending_echoes: dict[str, asyncio.Future] = {}

    async def send_action(self, action: str, params: dict = None, echo: str = None,
                          timeout: float = 10.0) -> dict:
        payload = {"action": action, "params": params or {}}
        future: Optional[asyncio.Future] = None
        if echo:
            payload["echo"] = echo
        elif timeout > 0:
            echo = str(uuid.uuid4())
            payload["echo"] = echo

        if echo and timeout > 0:
            future = asyncio.get_running_loop().create_future()
            self._pending_echoes[echo] = future

        # 兼容FastAPI WebSocket和原生websockets
        try:
            if hasattr(self.websocket, 'send_json'):
                await self.websocket.send_json(payload)
            else:
                await self.websocket.send(json.dumps(payload))
        except Exception:
            if echo and timeout > 0:
                self._pending_echoes.pop(echo, None)
                if future and not future.done():
                    future.cancel()
            raise

        if echo and timeout > 0:
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Action '{action}' echo '{echo}' timed out on port {self.port}")
                return {"status": "error", "retcode": -1, "message": "timeout"}
            finally:
                self._pending_echoes.pop(echo, None)

        return payload

    def resolve_echo(self, echo: str, data: dict) -> bool:
        future = self._pending_echoes.pop(echo, None)
        if future and not future.done():
            future.set_result(data)
            return True
        return False

    async def send_message(self, group_id: str = None, user_id: str = None,
                           message: list = None):
        if not group_id and not user_id:
            raise ValueError("Either group_id or user_id must be provided")
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
        self._port_configs: dict[int, dict] = {}
        self._servers: list[websockets.asyncio.server.Server] = []

        for i in range(num_ports):
            port = base_port + i
            self.connections[port] = None

    def set_port_configs(self, port_configs: dict[int, dict]):
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

    async def _validate_token(self, port: int, websocket: WebSocket) -> bool:
        port_config = self._port_configs.get(port, {})
        required_token = port_config.get("token", "")
        if not required_token:
            return True

        path = str(websocket.url.path) if websocket.url else "/"
        headers = websocket.headers

        query_string = str(websocket.url.query_string) if websocket.url else ""
        query_params = urllib.parse.parse_qs(query_string)
        token_from_query = query_params.get("access_token", [None])[0]

        token_from_header = None
        auth_header = headers.get("authorization", "") if headers else ""
        if auth_header.startswith("Bearer "):
            token_from_header = auth_header[7:]

        if token_from_query == required_token or token_from_header == required_token:
            return True

        logger.warning(f"Token validation failed for port {port}")
        return False

    async def _close_old_connection(self, port: int):
        old_conn = self.connections.get(port)
        if old_conn:
            logger.info(f"Closing existing connection on port {port}")
            await old_conn.close()
            self.connections[port] = None

    async def handle_connection(self, port: int, websocket: WebSocket):
        from datetime import datetime

        await websocket.accept()

        if not await self._validate_token(port, websocket):
            await websocket.close(code=4001, reason="Unauthorized")
            return

        await self._close_old_connection(port)

        conn = NapCatConnection(port, websocket)
        self.connections[port] = conn
        conn.connected_at = datetime.now()

        logger.info(f"New connection on port {port}")

        if self._status_callback:
            await self._status_callback("connected", port)

        try:
            while True:
                data = await websocket.receive_json()
                conn.last_message_at = datetime.now()
                await self._process_event(port, data, conn)
        except WebSocketDisconnect:
            logger.info(f"Client disconnected from port {port}")
        except Exception as e:
            logger.error(f"Error on port {port}: {e}")
        finally:
            if self.connections.get(port) is conn:
                self.connections[port] = None
            if self._status_callback:
                await self._status_callback("disconnected", port)

    async def _process_event(self, port: int, data: dict, conn: NapCatConnection = None):
        post_type = data.get("post_type", "")

        if post_type == "meta_event":
            sub_type = data.get("sub_type", "")
            if sub_type == "connect":
                self_id = str(data.get("self_id", ""))
                target_conn = conn or self.connections.get(port)
                if target_conn and self_id:
                    target_conn.qq_id = self_id
                    logger.info(f"Port {port} identified as QQ {self_id}")

                    try:
                        login_info = await target_conn.send_action("get_login_info", timeout=5.0)
                        if login_info and login_info.get("status") == "ok":
                            data = login_info.get("data", {})
                            target_conn.qq_name = data.get("nickname", "")
                            logger.info(f"Port {port} QQ name: {target_conn.qq_name}")
                    except Exception as e:
                        logger.warning(f"Failed to get login info for port {port}: {e}")

                    if self._status_callback:
                        await self._status_callback("identified", port, self_id)

        if data.get("echo"):
            target_conn = conn or self.connections.get(port)
            if target_conn and target_conn.resolve_echo(data["echo"], data):
                return

        for handler in self.event_handlers:
            try:
                await handler(port, data)
            except Exception as e:
                logger.error(f"Event handler error: {e}")

    async def send_to_port(self, port: int, action: str, params: dict = None,
                           timeout: float = 10.0) -> Optional[dict]:
        conn = self.connections.get(port)
        if not conn:
            logger.warning(f"No connection on port {port}")
            return None
        return await conn.send_action(action, params, timeout=timeout)

    async def broadcast(self, action: str, params: dict = None):
        for port, conn in self.connections.items():
            if conn:
                try:
                    await conn.send_action(action, params, timeout=0)
                except Exception as e:
                    logger.error(f"Broadcast error on port {port}: {e}")

    def is_connected(self, port: int) -> bool:
        return self.connections.get(port) is not None

    def get_connection(self, port: int) -> Optional[NapCatConnection]:
        return self.connections.get(port)

    async def start_servers(self):
        """为每个端口启动独立的WebSocket服务器"""
        for i in range(self.num_ports):
            port = self.base_port + i
            try:
                server = await websockets.serve(
                    lambda ws, p=port: self._handle_ws_connection(p, ws),
                    self.host,
                    port
                )
                self._servers.append(server)
                logger.info(f"WebSocket server started on port {port}")
            except Exception as e:
                logger.error(f"Failed to start WebSocket server on port {port}: {e}")

    async def _handle_ws_connection(self, port: int, websocket: ServerConnection):
        """处理单个端口的WebSocket连接"""
        from datetime import datetime

        # 获取远程地址（安全处理）
        remote = websocket.remote_address
        client_addr = f"{remote[0]}:{remote[1]}" if remote else "unknown"
        logger.info(f"New connection on port {port} from {client_addr}")

        # 验证token（从查询参数或header获取）
        if not await self._validate_token_ws(port, websocket):
            logger.warning(f"Token validation failed for port {port} from {client_addr}")
            await websocket.close(4001, "Unauthorized")
            return

        await self._close_old_connection(port)

        conn = NapCatConnection(port, websocket)
        self.connections[port] = conn
        conn.connected_at = datetime.now()

        if self._status_callback:
            await self._status_callback("connected", port)

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    conn.last_message_at = datetime.now()
                    await self._process_event(port, data, conn)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received on port {port}")
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"Client disconnected from port {port}: {e.code} {e.reason}")
        except Exception as e:
            logger.error(f"Error on port {port}: {e}")
        finally:
            if self.connections.get(port) is conn:
                self.connections[port] = None
            if self._status_callback:
                await self._status_callback("disconnected", port)

    async def _validate_token_ws(self, port: int, websocket: ServerConnection) -> bool:
        """验证WebSocket连接的token"""
        port_config = self._port_configs.get(port, {})
        required_token = port_config.get("token", "")
        if not required_token:
            return True

        # 从查询参数获取token
        path = websocket.request.path if websocket.request else "/"
        query = websocket.request.headers.get("Query-String", "") if websocket.request else ""
        
        # 解析查询参数
        import urllib.parse as urlparse
        query_params = urlparse.parse_qs(query)
        token_from_query = query_params.get("access_token", [None])[0]

        # 从header获取token
        token_from_header = None
        auth_header = websocket.request.headers.get("Authorization", "") if websocket.request else ""
        if auth_header.startswith("Bearer "):
            token_from_header = auth_header[7:]

        if token_from_query == required_token or token_from_header == required_token:
            return True

        logger.warning(f"Token validation failed for port {port}")
        return False

    async def stop_servers(self):
        """关闭所有WebSocket服务器"""
        for server in self._servers:
            try:
                server.close()
                await server.wait_closed()
            except Exception as e:
                logger.error(f"Error closing server: {e}")
        self._servers.clear()
