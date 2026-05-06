# Hermes Dashboard

Hermes Agent 可视化控制面板，基于 Apple Design System（暗色主题）。

## 技术栈

- **前端**：纯 HTML + CSS + Vanilla JS，零框架依赖
- **后端**：Python Flask（port 3801）
- **前端代理**：Python 内置 HTTP Server（port 3800），`/api/*` 代理到后端
- **数据源**：Hermes Agent SQLite（`~/.hermes/state.db`）

## 项目结构

```
hermes-dashboard/
├── SPEC.md                  # 设计规范
├── README.md
├── .gitignore
├── backend/
│   ├── app.py               # Flask API
│   └── requirements.txt
├── frontend/
│   ├── index.html            # SPA
│   └── css/style.css
├── server.py                 # 前端代理服务
└── start.sh                  # 一键启动
```

## 快速启动

```bash
cd hermes-dashboard
./start.sh
```

然后访问 **http://localhost:3800**

## 功能模块

- 系统概览（会话/消息/Token/费用）
- Token 配额追踪（每轮/每周/每月）
- 频道状态（Telegram / Weixin / Discord / Slack 等）
- 会话管理
- 技能列表
- Cron 定时任务
- 系统日志
- GitHub Release 更新日志

## 配置

启动前确保 `HERMES_HOME` 环境变量指向正确的 Hermes 配置目录（默认 `~/.hermes`）：

```bash
export HERMES_HOME="$HOME/.hermes"
./start.sh
```

## 依赖

```
flask
flask-cors
psutil
pyyaml
```
