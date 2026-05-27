const API_BASE = '';

const PAGE_TITLES = {
    dashboard: '仪表盘',
    server: '本地参数配置',
    llm: 'LLM 配置',
    chat: '聊天配置',
};

class CircleOnlineApp {
    constructor() {
        this.state = {
            enabled: true,
            ports: {},
            characters: [],
            assignments: {},
        };
        this.config = null;
        this.currentPage = 'dashboard';
        this.refreshInterval = null;
        this.token = localStorage.getItem('auth_token') || '';
        this._selectedDispatcherPreset = 'balanced';
    }

    async init() {
        const valid = await this.checkAuth();
        if (!valid) {
            this.showLogin();
            return;
        }
        this.showApp();
        this.bindEvents();
        this.initPasswordFields();
        await this.fetchStatus();
        this.startAutoRefresh();
        this.showToast('系统已就绪', 'success');
    }

    async checkAuth() {
        if (!this.token) return false;
        try {
            const res = await fetch(`${API_BASE}/api/auth/check`, {
                headers: { 'Authorization': `Bearer ${this.token}` },
            });
            if (!res.ok) return false;
            const data = await res.json();
            return data.authenticated === true;
        } catch {
            return false;
        }
    }

    showLogin() {
        document.getElementById('loginOverlay').style.display = 'flex';
        document.getElementById('appShell').style.display = 'none';
        document.getElementById('loginPassword').focus();
    }

    showApp() {
        document.getElementById('loginOverlay').style.display = 'none';
        document.getElementById('appShell').style.display = 'flex';
    }

    async login(event) {
        event.preventDefault();
        const password = document.getElementById('loginPassword').value;
        const errorEl = document.getElementById('loginError');
        errorEl.textContent = '';

        try {
            const res = await fetch(`${API_BASE}/api/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password }),
            });
            if (!res.ok) {
                const data = await res.json();
                errorEl.textContent = data.detail || '密码错误';
                return;
            }
            const data = await res.json();
            this.token = data.token;
            localStorage.setItem('auth_token', this.token);
            document.getElementById('loginPassword').value = '';
            this.showApp();
            this.bindEvents();
            this.initPasswordFields();
            await this.fetchStatus();
            this.startAutoRefresh();
            this.showToast('登录成功', 'success');
        } catch (e) {
            errorEl.textContent = '连接失败: ' + e.message;
        }
    }

    logout() {
        fetch(`${API_BASE}/api/auth/logout`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${this.token}` },
        }).catch(() => {});
        this.token = '';
        localStorage.removeItem('auth_token');
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
        this.showLogin();
    }

    authHeaders() {
        return {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this.token}`,
        };
    }

    async authedFetch(url, options = {}) {
        if (!options.headers) options.headers = {};
        options.headers['Authorization'] = `Bearer ${this.token}`;
        const res = await fetch(url, options);
        if (res.status === 401) {
            this.logout();
            this.showToast('登录已过期，请重新登录', 'error');
            throw new Error('Unauthorized');
        }
        return res;
    }

    initPasswordFields() {
        const apiKeyInputs = ['cfg-api_key', 'cfg-vision_api_key'];
        apiKeyInputs.forEach(id => {
            const input = document.getElementById(id);
            if (input) {
                input.classList.add('password-hidden');
                input.style.webkitTextSecurity = 'disc';
            }
        });
    }

    bindEvents() {
        document.getElementById('orchestratorToggle').addEventListener('change', () => this.toggleOrchestrator());

        const prefix = document.getElementById('cfg-prompt_prefix');
        const suffix = document.getElementById('cfg-prompt_suffix');
        if (prefix) prefix.addEventListener('input', () => this.updatePreview());
        if (suffix) suffix.addEventListener('input', () => this.updatePreview());
        const timeAware = document.getElementById('cfg-time_awareness');
        if (timeAware) timeAware.addEventListener('change', () => this.updatePreview());

        const autoDialogueEnabled = document.getElementById('cfg-auto_dialogue_enabled');
        if (autoDialogueEnabled) {
            autoDialogueEnabled.addEventListener('change', (e) => {
                document.getElementById('autoDialogueToggleLabel').textContent =
                    e.target.checked ? '已启用' : '已禁用';
            });
        }
    }

    startAutoRefresh() {
        this.refreshInterval = setInterval(() => {
            if (this.currentPage === 'dashboard') {
                this.fetchStatus();
            }
        }, 5000);
    }

    // ── Navigation ──
    navigateTo(page) {
        this.currentPage = page;

        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        const target = document.getElementById(`page-${page}`);
        if (target) target.classList.add('active');

        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        const navBtn = document.querySelector(`.nav-item[data-page="${page}"]`);
        if (navBtn) navBtn.classList.add('active');

        document.getElementById('pageTitle').textContent = PAGE_TITLES[page] || page;

        if (page !== 'dashboard') {
            this.loadConfig();
        }
    }

    // ── Status Fetch (Dashboard) ──
    async fetchStatus() {
        try {
            const response = await this.authedFetch(`${API_BASE}/api/status`);
            if (!response.ok) throw new Error('Failed to fetch status');
            const data = await response.json();
            this.updateState(data);
            this.render();
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                console.error('Fetch status error:', error);
            }
        }
    }

    updateState(data) {
        this.state.enabled = data.enabled;
        this.state.ports = data.ports;
        this.state.characters = data.characters;
        this.state.assignments = data.assignments;
        this.state.portConfigs = data.portConfigs || {};
    }

    render() {
        this.renderToggle();
        this.renderStats();
        this.renderPorts();
        this.renderAssignments();
    }

    renderToggle() {
        const toggle = document.getElementById('orchestratorToggle');
        const label = document.getElementById('toggleLabel');
        toggle.checked = this.state.enabled;
        label.textContent = this.state.enabled ? '运行中' : '已暂停';
        label.style.color = this.state.enabled
            ? 'var(--md-sys-color-success)'
            : 'var(--md-sys-color-error)';
    }

    renderStats() {
        const ports = Object.values(this.state.ports);
        const connectedCount = ports.filter(p => p.status === 'connected').length;
        const assignedCount = Object.keys(this.state.assignments).filter(k => this.state.assignments[k]).length;

        document.getElementById('connectedCount').textContent = connectedCount;
        document.getElementById('assignedCount').textContent = assignedCount;
        document.getElementById('characterCount').textContent = this.state.characters.length;
    }

    renderPorts() {
        const grid = document.getElementById('portsGrid');
        const ports = Object.entries(this.state.ports);

        grid.innerHTML = ports.map(([port, info]) => `
            <div class="port-card ${info.status}">
                <div class="port-header">
                    <span class="port-number">${port}</span>
                    <div class="port-status ${info.status}">
                        <span class="port-status-dot"></span>
                        ${info.status === 'connected' ? '已连接' : '未连接'}
                    </div>
                </div>
                <div class="port-info">
                    <div class="port-info-item">
                        <span class="material-icons-round">label</span>
                        <span>${info.name || 'Slot ' + (port - 8080)}</span>
                    </div>
                    ${info.qq_id ? `
                        <div class="port-info-item">
                            <span class="material-icons-round">person</span>
                            <span class="port-qq-id">${info.qq_id}</span>
                        </div>
                    ` : ''}
                    ${info.character ? `
                        <div class="port-info-item">
                            <span class="material-icons-round">emoji_emotions</span>
                            <span>${info.character}</span>
                        </div>
                    ` : ''}
                </div>
            </div>
        `).join('');
    }

    renderAssignments() {
        const matrix = document.getElementById('assignmentMatrix');
        const ports = Object.keys(this.state.ports);

        matrix.innerHTML = ports.map(port => {
            const currentChar = this.state.assignments[port] || '';
            const portConfig = this.state.portConfigs?.[port] || {};
            const currentToken = portConfig.token || '';
            return `
                <div class="assignment-card">
                    <div class="assignment-header">
                        <span class="assignment-port">端口 ${port}</span>
                        ${this.state.ports[port]?.status === 'connected'
                            ? '<span class="port-status connected"><span class="port-status-dot"></span>在线</span>'
                            : '<span class="port-status disconnected"><span class="port-status-dot"></span>离线</span>'
                        }
                    </div>
                    <select
                        class="assignment-select"
                        data-port="${port}"
                        onchange="app.assignCharacter(${port}, this.value)"
                    >
                        <option value="">-- 未分配 --</option>
                        ${this.state.characters.map(char => `
                            <option value="${char.name}" ${char.name === currentChar ? 'selected' : ''}>
                                ${char.name}
                            </option>
                        `).join('')}
                    </select>
                    <div class="md3-field">
                        <label class="md3-label">访问令牌</label>
                        <input type="text" class="md3-input" id="cfg-token-${port}"
                               value="${currentToken}"
                               placeholder="可选，留空表示不需要鉴权"
                               autocomplete="new-password"
                               onchange="app.updatePortToken(${port}, this.value)">
                        <span class="md3-helper">用于鉴权的 access_token</span>
                    </div>
                </div>
            `;
        }).join('');
    }

    // ── Config Load/Save ──
    async loadConfig() {
        try {
            const response = await this.authedFetch(`${API_BASE}/api/config`);
            if (!response.ok) throw new Error('Failed to fetch config');
            this.config = await response.json();
            this.populateConfigForms();
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                this.showToast('加载配置失败: ' + error.message, 'error');
            }
        }
    }

    populateConfigForms() {
        if (!this.config) return;
        const c = this.config;

        this.setField('cfg-host', c.server.host);
        this.setField('cfg-management_port', c.server.management_port);
        this.setField('cfg-base_port', c.server.base_port);
        this.setField('cfg-num_ports', c.server.num_ports);

        this.setField('cfg-reply_delay_ms', c.orchestrator.reply_delay_ms);
        this.setField('cfg-max_context_messages', c.orchestrator.max_context_messages);
        this.setField('cfg-max_context_tokens', c.orchestrator.max_context_tokens);
        this.setField('cfg-group_reply_probability', c.orchestrator.group_reply_probability);

        this.setField('cfg-provider', c.llm.provider);
        this.setField('cfg-base_url', c.llm.base_url);
        this.setApiKeyField('cfg-api_key', c.llm.api_key_set);
        this.setField('cfg-model', c.llm.model);
        this.setField('cfg-model_thinking', c.llm.model_thinking);
        this.setField('cfg-assistant_model', c.llm.assistant_model);
        this.setField('cfg-assistant_model_thinking', c.llm.assistant_model_thinking);
        this.setField('cfg-temperature', c.llm.temperature);
        this.setField('cfg-max_tokens', c.llm.max_tokens);

        if (c.llm.vision) {
            const visionEnabled = document.getElementById('cfg-vision_enabled');
            if (visionEnabled) {
                visionEnabled.checked = c.llm.vision.enabled;
            }
            this.setField('cfg-vision_base_url', c.llm.vision.base_url);
            this.setApiKeyField('cfg-vision_api_key', c.llm.vision.api_key_set);
            this.setField('cfg-vision_model', c.llm.vision.model);
            this.setField('cfg-vision_thinking', c.llm.vision.thinking);
        }

        this.setField('cfg-prompt_prefix', c.orchestrator.prompt_prefix);
        this.setField('cfg-prompt_suffix', c.orchestrator.prompt_suffix);
        const timeAware = document.getElementById('cfg-time_awareness');
        if (timeAware) timeAware.checked = c.orchestrator.time_awareness;

        if (c.orchestrator.auto_dialogue) {
            const autoEnabled = document.getElementById('cfg-auto_dialogue_enabled');
            if (autoEnabled) {
                autoEnabled.checked = c.orchestrator.auto_dialogue.enabled;
                document.getElementById('autoDialogueToggleLabel').textContent =
                    c.orchestrator.auto_dialogue.enabled ? '已启用' : '已禁用';
            }
            this.setField('cfg-auto_dialogue_chain_length', c.orchestrator.auto_dialogue.chain_length);
            this.setField('cfg-auto_dialogue_cooldown_ms', c.orchestrator.auto_dialogue.cooldown_ms);
            this.setField('cfg-auto_dialogue_trigger_probability', c.orchestrator.auto_dialogue.trigger_probability);
            this.setField('cfg-auto_dialogue_initiation_probability', c.orchestrator.auto_dialogue.initiation_probability);
            this.setField('cfg-auto_dialogue_initiation_interval_ms', c.orchestrator.auto_dialogue.initiation_interval_ms);
        }

        if (c.chat) {
            this.setField('cfg-admin_qq', c.chat.admin_qq);
            this.setField('cfg-main_group_id', c.chat.main_group_id);
            const privateMsgEnabled = document.getElementById('cfg-private_message_enabled');
            if (privateMsgEnabled) {
                privateMsgEnabled.checked = c.chat.private_message_enabled;
            }
        }

        this.updatePreview();
        this.loadDispatcherConfig();
    }

    setField(id, value) {
        const el = document.getElementById(id);
        if (el && value !== undefined && value !== null) {
            el.value = value;
        }
    }

    getField(id) {
        const el = document.getElementById(id);
        return el ? el.value : null;
    }

    setApiKeyField(id, isSet) {
        const el = document.getElementById(id);
        if (!el) return;
        el.value = '';
        el.placeholder = isSet ? '已配置，留空保持不变' : '未配置';
    }

    async saveServerConfig() {
        const payload = {
            server: {
                host: this.getField('cfg-host'),
                management_port: parseInt(this.getField('cfg-management_port')) || null,
                base_port: parseInt(this.getField('cfg-base_port')) || null,
                num_ports: parseInt(this.getField('cfg-num_ports')) || null,
            },
        };
        await this.saveConfig(payload, '服务器配置已保存');
    }

    async saveOrchestratorConfig() {
        const payload = {
            orchestrator: {
                reply_delay_ms: parseInt(this.getField('cfg-reply_delay_ms')) || null,
                max_context_messages: parseInt(this.getField('cfg-max_context_messages')) || null,
                max_context_tokens: parseInt(this.getField('cfg-max_context_tokens')) || null,
                group_reply_probability: parseFloat(this.getField('cfg-group_reply_probability')) || null,
            },
        };
        await this.saveConfig(payload, '编排器配置已保存');
    }

    async saveLLMConfig() {
        const visionEnabled = document.getElementById('cfg-vision_enabled')?.checked;
        const apiKey = this.getField('cfg-api_key');
        const visionApiKey = this.getField('cfg-vision_api_key');
        const payload = {
            llm: {
                provider: this.getField('cfg-provider'),
                base_url: this.getField('cfg-base_url'),
                model: this.getField('cfg-model'),
                model_thinking: this.getField('cfg-model_thinking'),
                assistant_model: this.getField('cfg-assistant_model'),
                assistant_model_thinking: this.getField('cfg-assistant_model_thinking'),
                temperature: parseFloat(this.getField('cfg-temperature')) || null,
                max_tokens: parseInt(this.getField('cfg-max_tokens')) || null,
            },
            vision: {
                enabled: visionEnabled,
                base_url: this.getField('cfg-vision_base_url'),
                model: this.getField('cfg-vision_model'),
                thinking: this.getField('cfg-vision_thinking'),
            },
        };
        if (apiKey) payload.llm.api_key = apiKey;
        if (visionApiKey) payload.vision.api_key = visionApiKey;
        await this.saveConfig(payload, 'LLM 配置已保存');
    }

    async savePromptConfig() {
        const payload = {
            orchestrator: {
                prompt_prefix: this.getField('cfg-prompt_prefix'),
                prompt_suffix: this.getField('cfg-prompt_suffix'),
                time_awareness: document.getElementById('cfg-time_awareness')?.checked ?? null,
            },
        };
        await this.saveConfig(payload, '提示词配置已保存');
    }

    async saveAutoDialogueConfig() {
        const autoEnabled = document.getElementById('cfg-auto_dialogue_enabled')?.checked;
        const payload = {
            orchestrator: {
                auto_dialogue: {
                    enabled: autoEnabled,
                    chain_length: parseInt(this.getField('cfg-auto_dialogue_chain_length')) || null,
                    cooldown_ms: parseInt(this.getField('cfg-auto_dialogue_cooldown_ms')) || null,
                    trigger_probability: parseFloat(this.getField('cfg-auto_dialogue_trigger_probability')) || null,
                    initiation_probability: parseFloat(this.getField('cfg-auto_dialogue_initiation_probability')) || null,
                    initiation_interval_ms: parseInt(this.getField('cfg-auto_dialogue_initiation_interval_ms')) || null,
                },
            },
        };

        try {
            const response = await this.authedFetch(`${API_BASE}/api/orchestrator/auto-dialogue/config`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload.orchestrator.auto_dialogue),
            });
            if (!response.ok) throw new Error('Save failed');
            const data = await response.json();
            this.showToast(data.message || '自动对话配置已保存', 'success');

            document.getElementById('autoDialogueToggleLabel').textContent =
                autoEnabled ? '已启用' : '已禁用';
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                this.showToast('保存失败: ' + error.message, 'error');
            }
        }
    }

    // ── Dispatcher Preset ──
    selectDispatcherPreset(presetKey) {
        this._selectedDispatcherPreset = presetKey;
        document.querySelectorAll('.preset-card').forEach(card => {
            card.classList.toggle('selected', card.dataset.preset === presetKey);
        });
    }

    async saveDispatcherConfig() {
        const dispatcherEnabled = document.getElementById('cfg-dispatcher_enabled')?.checked;
        const supremePower = document.getElementById('cfg-dispatcher_supreme_power')?.checked;
        const customPrompt = this.getField('cfg-dispatcher_prompt');
        const payload = {
            enabled: dispatcherEnabled,
            supreme_power: supremePower,
            dispatcher_preset: this._selectedDispatcherPreset,
            dispatcher_prompt: customPrompt || '',
        };

        try {
            const response = await this.authedFetch(`${API_BASE}/api/dispatcher/config`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) throw new Error('Save failed');
            const data = await response.json();
            this.showToast(data.message || '调度器配置已保存', 'success');
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                this.showToast('保存失败: ' + error.message, 'error');
            }
        }
    }

    async loadDispatcherConfig() {
        try {
            const response = await this.authedFetch(`${API_BASE}/api/dispatcher/config`);
            if (!response.ok) throw new Error('Failed to fetch');
            const data = await response.json();
            this._selectedDispatcherPreset = data.dispatcher_preset || 'balanced';
            this.selectDispatcherPreset(this._selectedDispatcherPreset);
            this.setField('cfg-dispatcher_prompt', data.dispatcher_prompt);
            const dispatcherEnabled = document.getElementById('cfg-dispatcher_enabled');
            if (dispatcherEnabled) dispatcherEnabled.checked = data.enabled;
            const supremePower = document.getElementById('cfg-dispatcher_supreme_power');
            if (supremePower) supremePower.checked = data.supreme_power;
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                console.error('Load dispatcher config error:', error);
            }
        }
    }

    async saveChatConfig() {
        const privateMsgEnabled = document.getElementById('cfg-private_message_enabled')?.checked;
        const dashboardPassword = this.getField('cfg-dashboard_password');
        const payload = {
            admin_qq: this.getField('cfg-admin_qq'),
            main_group_id: this.getField('cfg-main_group_id'),
            private_message_enabled: privateMsgEnabled,
        };
        if (dashboardPassword) {
            payload.dashboard_password = dashboardPassword;
        }

        try {
            const response = await this.authedFetch(`${API_BASE}/api/chat/config`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) throw new Error('Save failed');
            const data = await response.json();
            this.showToast(data.message || '聊天配置已保存', 'success');
            document.getElementById('cfg-dashboard_password').value = '';
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                this.showToast('保存失败: ' + error.message, 'error');
            }
        }
    }

    async saveConfig(payload, successMessage) {
        try {
            const response = await this.authedFetch(`${API_BASE}/api/config`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) throw new Error('Save failed');
            const data = await response.json();
            this.showToast(successMessage || data.message || '配置已保存', 'success');
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                this.showToast('保存失败: ' + error.message, 'error');
            }
        }
    }

    // ── Prompt Preview ──
    togglePreview() {
        const body = document.getElementById('promptPreviewBody');
        const arrow = document.getElementById('previewArrow');
        if (body.style.display === 'none') {
            body.style.display = 'flex';
            arrow.classList.add('expanded');
        } else {
            body.style.display = 'none';
            arrow.classList.remove('expanded');
        }
    }

    updatePreview() {
        const prefix = document.getElementById('cfg-prompt_prefix')?.value || '';
        const suffix = document.getElementById('cfg-prompt_suffix')?.value || '';
        const timeAware = document.getElementById('cfg-time_awareness')?.checked;

        document.getElementById('previewPrefix').textContent = prefix || '(空)';
        document.getElementById('previewSuffix').textContent = suffix || '(空)';

        const timeBlock = document.getElementById('previewTimeBlock');
        if (timeBlock) {
            timeBlock.style.display = timeAware ? 'block' : 'none';
        }
    }

    // ── Actions ──
    async toggleOrchestrator() {
        try {
            const response = await this.authedFetch(`${API_BASE}/api/orchestrator/toggle`, { method: 'POST' });
            if (!response.ok) throw new Error('Failed to toggle');
            const data = await response.json();
            this.state.enabled = data.enabled;
            this.renderToggle();
            this.showToast(data.enabled ? '编排器已启用' : '编排器已暂停', 'success');
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                this.showToast('切换失败: ' + error.message, 'error');
            }
        }
    }

    async assignCharacter(port, characterName) {
        try {
            const response = await this.authedFetch(`${API_BASE}/api/assign`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ port, character_name: characterName }),
            });
            if (!response.ok) throw new Error('Failed to assign');

            if (characterName) {
                this.state.assignments[port] = characterName;
            } else {
                delete this.state.assignments[port];
            }
            this.renderStats();
            this.showToast(
                characterName ? `已分配 ${characterName} 到端口 ${port}` : `已清除端口 ${port} 的分配`,
                'success'
            );
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                this.showToast('分配失败: ' + error.message, 'error');
            }
        }
    }

    async updatePortToken(port, token) {
        try {
            const response = await this.authedFetch(`${API_BASE}/api/port-token`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ port, token }),
            });
            if (!response.ok) throw new Error('Failed to update token');
            this.showToast(`端口 ${port} 的访问令牌已更新`, 'success');
        } catch (error) {
            if (error.message !== 'Unauthorized') {
                this.showToast('更新令牌失败: ' + error.message, 'error');
            }
        }
    }

    togglePasswordVisibility(inputId) {
        const input = document.getElementById(inputId);
        if (!input) return;
        const btn = input.parentElement.querySelector('.md3-icon-btn .material-icons-round');
        if (input.classList.contains('password-hidden')) {
            input.classList.remove('password-hidden');
            input.style.webkitTextSecurity = 'none';
            if (btn) btn.textContent = 'visibility_off';
        } else {
            input.classList.add('password-hidden');
            input.style.webkitTextSecurity = 'disc';
            if (btn) btn.textContent = 'visibility';
        }
    }

    // ── Toast ──
    showToast(message, type = 'info') {
        const container = document.getElementById('toastContainer');
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => {
            toast.style.animation = 'slideOut 0.3s ease forwards';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }
}

const app = new CircleOnlineApp();
app.init();
