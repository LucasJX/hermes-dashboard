# Hermes Dashboard 🦞

Hermes Agent 的可视化管理面板。Apple HIG 设计语言，液态玻璃（Liquid Glass）UI 风格，实时监控 Channels、Sessions、Models、Skills、Logs、Quota 等核心数据。

> ⚡ 适用于 [Hermes Agent](https://github.com/LucasJX/hermes-agent) 用户。部署后通过浏览器管理你的 AI Agent。

---

## 🔑 默认登录

| 项目 | 值 |
|------|-----|
| **用户名** | `admin` |
| **密码** | `admin` |

首次部署时后端自动创建默认管理员账户。**请登录后立即修改密码**。

---

## ✨ 功能一览

### 📊 Dashboard（首页）
- Hero 统计卡片：Channels 数量、Sessions 数量、Models 数量、Skills 数量
- Hermes Agent 版本信息卡片（液态玻璃风格，蓝色渐变侧边条）
- 更新日志面板（点击展开，瀑布式文字揭露动画）
- 配额卡片：MiniMax 配额 + 通用 Provider 使用统计
- 频道状态卡片：各平台连接状态 + 独立会话/Token 统计
- 活跃会话卡片：最近活跃的会话列表

### 💬 Channels（频道）
- 平台状态监控：Telegram、Weixin、Discord、Slack、Matrix、Signal 等
- 每个平台独立的 Session / Token 统计
- 频道详细信息查看

### 📋 Sessions（会话）
- 按来源分组折叠展示
- 消息查看器：查看会话详细消息内容
- 会话详情面板

### 🤖 Models（模型）
- Provider 管理（MiniMax、xiaomi、OpenRouter、Anthropic 等）
- 模型列表与配置
- 配置编辑器

### 🧩 Skills（技能）
- 已安装技能浏览器
- SKILL.md 内容查看器
- 技能分类展示

### 📜 Logs（日志）
- 多文件日志查看：agent.log、errors.log、gateway.log
- 按级别过滤：INFO、WARNING、ERROR
- 关键词搜索
- 最新优先排列
- 自定义 4px 细滚动条

### 📈 Quota（配额）
- MiniMax 配额条：单轮 / 每周 / 每月
- 通用 Provider 使用统计：Token 用量 / 费用（从 sessions DB 读取）

### 👤 Account（账户）
- 登录 / 登出
- 密码修改
- Session Token 管理（绕过反向代理 Cookie 问题）

---

## 🚀 快速开始

### 环境要求

| 依赖 | 版本 |
|------|------|
| Python | 3.10+ |
| Hermes Agent | 已安装且配置（`~/.hermes/` 目录存在） |
| Flask | — |
| flask-cors | — |
| psutil | — |
| PyYAML | — |

### 安装与启动

```bash
git clone https://github.com/LucasJX/hermes-dashboard.git
cd hermes-dashboard
chmod +x start.sh
./start.sh
```

`start.sh` 会自动：
1. 创建 Python venv（如不存在）
2. 安装依赖
3. 启动后端 API（端口 3801）
4. 启动前端静态文件服务（端口 3800）

### 访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端页面 | `http://localhost:3800` | 浏览器打开，输入 `admin / admin` 登录 |
| 后端 API | `http://localhost:3801` | Flask API，由前端自动代理 |

### 登录后操作

1. 使用 `admin / admin` 登录
2. 进入 Account 页面修改默认密码
3. 如需反向代理（Nginx），参考下方配置

---

## 🏗️ 架构

```
┌─────────────────────────────────────────────┐
│  browser (http://localhost:3800)             │
│  frontend/index.html — 单页应用              │
│  vanilla JS + CSS (液态玻璃风格)              │
└──────────────┬──────────────────────────────┘
               │ fetch('/api/...')
               ▼
┌─────────────────────────────────────────────┐
│  server.py (port 3800)                      │
│  静态文件 + 反向代理到后端 API                 │
└──────────────┬──────────────────────────────┘
               │ proxy_pass
               ▼
┌─────────────────────────────────────────────┐
│  backend/app.py (port 3801)                 │
│  Flask API，读取 Hermes 状态                  │
│  • ~/.hermes/hermes_state.db (sessions)     │
│  • ~/.hermes/auth.json (credentials)        │
│  • ~/.hermes/config.yaml (配置)              │
│  • ~/.hermes/dashboard_auth.db (登录账户)    │
│  • ~/.hermes/logs/ (日志文件)                │
└─────────────────────────────────────────────┘
```

---

## ⚙️ 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HERMES_HOME` | `~/.hermes` | Hermes Agent 配置目录 |
| `DASHBOARD_SECRET` | 随机生成 | Flask Session 密钥（建议固定值，防止重启后 Token 失效） |

---

## 📁 项目结构

```
hermes-dashboard/
├── README.md              ← 本文件
├── start.sh               ← 一键启动脚本
├── server.py              ← 前端静态文件服务 + API 反向代理 (port 3800)
├── backend/
│   ├── app.py             ← Flask API 后端 (port 3801)
│   └── .auth_tokens.db    ← 登录 Token 数据库（运行时自动创建）
├── frontend/
│   ├── index.html         ← 单页应用（HTML + 内联 JS + 内联 CSS）
│   └── css/
│       └── style.css      ← 液态玻璃样式表
└── .venv/                 ← Python 虚拟环境（运行时自动创建）
```

---

## 🔧 反向代理配置（Nginx）

如果部署在公网，建议通过 Nginx 反向代理：

```nginx
server {
    listen 443 ssl;
    server_name dashboard.example.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:3800;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> ⚠️ 反向代理下 Cookie 可能丢失，Dashboard 已内置 Token-based 认证绕过此问题。登录后会自动获取 Bearer Token。

---

## 🎨 设计系统

### 液态玻璃（Liquid Glass）
- `backdrop-filter: blur(28-32px) saturate(200%)`
- 半透明背景 + 微弱边框
- 自适应亮色/暗色模式

### 字体
- 正文：系统默认字体栈（-apple-system, BlinkMacSystemFont, "Segoe UI", ...）
- 等宽：SFMono-Regular, Menlo, Monaco, Consolas

### 颜色
- 主题色：`#0066cc`（Apple HIG Blue）
- 成功：`#34c759`
- 警告：`#ff9f0a`
- 错误：`#ff3b30`

### 组件
- 导航栏：浮动胶囊 Dock 栏（液态玻璃风格）
- 卡片：液态玻璃卡片 + 蓝色渐变侧边条
- 按钮：胶囊形，hover 渐变背景
- 滚动条：4px 细滚动条，自定义 Webkit 样式

---

## 🛡️ 安全说明

| 机制 | 说明 |
|------|------|
| 密码哈希 | werkzeug bcrypt（`generate_password_hash`） |
| 暴力破解防护 | 5 次失败锁定 5 分钟 |
| Token 认证 | 7 天有效期，支持 Bearer Header |
| 默认账户 | 首次启动自动创建 `admin/admin`，请登录后修改 |
| 数据库位置 | `~/.hermes/dashboard_auth.db` |

---

## 📝 更新日志

### v2.15.1 (2026-05-14)
- Dark mode：修复卡片、TopDock、下拉菜单、手机 Dock 的白色边框，统一改为深色边框
- 移除全站浏览器 focus outline（白色晕圈）
- 新增从 `.env` 文件自动识别 API Provider（无需手动配置）

### v2.15.0 (2026-05-14)
- 修复导航栏 hover 白色方块问题
- 新增 Pacman 加载动画

### v2.14.0 (2026-05-12)
- 完整重写 UI，恢复所有 CSS（之前重写丢失的样式全部还原）
- 液态玻璃 Dock 栏重设计：高度统一 40px、圆角 20px、浮动胶囊风格
- 版本信息卡片：蓝色渐变侧边条 + 磨砂 Logo + changelog 瀑布式揭露动画
- 修复 TopDock Dark Mode 边框颜色
- 主题切换与头像移入 TopDock 容器内
- 下拉菜单 open class 修复
- 简化主题/头像图标为纯文本
- 版本路径修复（hermes → hermes-agent）

### v2.13.x
- MiniMax → OpenCC 迁移（API 端点变更）
- Proxy HTTPError 透传修复
- Session 认证修复

### v2.12.0
- 液态玻璃 UI 重设计
- 浮动胶囊 Dock 栏
- 版本信息卡片（蓝色渐变侧边 + 磨砂圆 logo）
- 更新日志瀑布式揭露动画
- 频道状态 + 活跃会话双卡片
- 自定义 4px 细滚动条
- Focus outline 移除

### v1.x
- 初始版本：Dashboard、Channels、Sessions、Models、Skills、Logs
- Token-based 认证系统
- MiniMax 配额统计

---

## 📄 License

MIT

---

**Built for Hermes Agent 🦞**
