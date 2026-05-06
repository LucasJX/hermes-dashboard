# Hermes Dashboard — 设计规范

> 基于 Apple Design System (Web)，适配暗色控制面板场景。

---

## 1. 设计理念

Terminal 的灵魂，macOS 的脸。
纯暗色主题，零框架依赖，单一动效锚点（`scale(0.95)` 按压），单一强调色 `#0066cc`。

---

## 2. 色彩系统

### 表面层（Dashboard 深色主题）
```
--canvas:       #0d0d0f   /* 主画布 */
--tile-1:       #1a1a1e   /* 卡片背景 */
--tile-2:       #232328   /* 悬停/高亮层 */
--tile-3:       #2d2d33   /* 选中态 */
--border:       #3a3a42   /* 分割线 */
```

### 文字层
```
--ink:          #ffffff   /* 主文字 */
--ink-2:        #a1a1a6   /* 次要文字 */
--ink-3:        #6e6e73   /* 占位/禁用 */
```

### 强调色（唯一）
```
--blue:         #0066cc   /* 主交互色 */
--blue-hover:   #0077ed   /* 悬停态 */
--blue-active:  #005bb5   /* 按压态 */
```

### 状态色（灰阶，语义替代）
```
--green:        #30d158   /* 在线/成功 */
--yellow:       #ffd60a   /* 警告 */
--red:          #ff453a   /* 错误/离线 */
--gray:         #8e8e93   /* 未知/空闲 */
```

---

## 3. 排版

```
font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'SF Pro Display', system-ui, sans-serif

/* 标题 */
h1:  28px / 600 / -0.3px  tracking   (section titles)
h2:  21px / 600 / -0.2px  tracking   (card titles)
h3:  14px / 600 / -0.1px  tracking   (labels)

/* 正文 */
body:  13px / 400 / 0       / 1.5  line-height
mono:  12px / 400 / 0       / 1.4  (token/count 数字)
fine:  11px / 400 / 0       / 1.3  (脚注/时间戳)
```

---

## 4. 间距

```
base: 4px
xs:   4px
sm:   8px
md:   12px
lg:   16px
xl:   24px
xxl:  32px
```

---

## 5. 圆角

```
sm:   6px   (按钮/输入框)
md:   10px  (小卡片)
lg:   14px  (大卡片)
pill: 9999px (标签/胶囊)
```

---

## 6. 动效

- **按压**: `transform: scale(0.95)` → 0.12s ease
- **悬停**: `background-color` 过渡 0.15s
- **入场**: `opacity 0→1, translateY(8px→0)` 0.25s ease-out，stagger 60ms
- **刷新**: 数据刷新时对应卡片区域 `opacity 0.5→1` 闪一下
- **进度条**: `width` 过渡 0.4s ease

---

## 7. 布局结构

```
┌─ Nav Bar (44px, 黑色) ─────────────────────────────────┐
│  🦞 Hermes    [主页] [频道] [会话] [Cron] [日志] [终端]   │
└────────────────────────────────────────────────────────┘
┌─ Content ──────────────────────────────────────────────┐
│                                                          │
│  ┌─ 系统概览 (4列 grid) ──────────────────────────────┐  │
│  │  [会话数] [消息数] [Token消耗] [运行时间]           │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Token配额 ────────────────────────────────────────┐  │
│  │  进度条 + 数字 + 刷新按钮                           │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ 频道状态 ────────────────────────────────────────┐  │
│  │  Telegram │ Weixin │ Discord │ Slack              │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ 最近会话 ────────────────────────────────────────┐  │
│  │  列表卡片，点击展开详情                             │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ 更新日志 ────────────────────────────────────────┐  │
│  │  AI翻译 + 分类标签 + 错开入场动画                   │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
└────────────────────────────────────────────────────────┘
```

---

## 8. 功能模块

### 8.1 系统概览
- 会话总数 / 消息总数 / Token消耗估算 / 运行时间
- 数据来源：SQLite `sessions` + `messages` 表
- Token费用：后端按模型定价表实时计算

### 8.2 Token配额追踪
- MiniMax Token Plan 配额（周额度 + 每轮额度）
- 进度条动态渲染（有哪个字段显示哪个）
- 数据来源：`mmx quota show` CLI

### 8.3 频道状态
- Telegram / Weixin / Discord / Slack
- 每频道：Logo + 名称 + 在线状态(圆点) + 延迟 + 最后活跃时间
- 连接状态来源：Hermes Gateway 进程状态

### 8.4 会话管理
- 所有会话列表，按频道筛选
- 列表项：会话ID / 来源 / 模型 / 消息数 / Token数 / 开始时间
- 点击展开：消息历史 + Token详情

### 8.5 技能列表
- 读取 `~/.hermes/skills/` 目录
- 分类展示：creative / devops / mlops 等

### 8.6 Cron 定时任务
- 任务列表、状态、cron表达式
- 支持暂停/恢复/立即执行
- 来源：`hermes cron list` 或 cronjob tool

### 8.7 系统日志
- 多日志文件切换：agent.log / gateway.log / errors.log
- 关键词搜索 + 级别筛选（ERROR/WARNING/INFO）
- 来源：`~/.hermes/logs/`

### 8.8 在线终端
- 输入命令 → 后端执行 → 返回结果
- 常用命令速查表

### 8.9 更新日志
- 自动拉取 GitHub Release（用 GitHub API）
- AI 翻译为中文（MiniMax）
- 分类卡片：新功能/平台扩展/核心改进/安全修复
- 逐字擦除动画入场

---

## 9. 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | 纯 HTML + CSS + Vanilla JS，零框架 |
| 后端 | Python Flask (port 3801) |
| 前端代理 | Python (port 3800，`/api/*` 代理到 3801) |
| 数据 | SQLite (`~/.hermes/state.db`) |
| 主题 | CSS 变量驱动暗色主题 |

---

## 10. 项目结构

```
hermes-dashboard/
├── SPEC.md
├── backend/
│   ├── app.py              # Flask API (3801)
│   └── requirements.txt
├── frontend/
│   ├── index.html          # SPA 主页面
│   └── css/
│       └── style.css       # 所有样式
├── server.py               # 前端静态文件 + API 代理 (3800)
└── start.sh                # 一键启动
```

---

## 11. API 设计

```
GET  /api/stats                    系统概览
GET  /api/quota                    Token配额
GET  /api/channels                 频道状态
GET  /api/sessions                 会话列表 (?source=telegram)
GET  /api/sessions/:id             会话详情
GET  /api/skills                   技能列表
GET  /api/cron                     Cron任务列表
POST /api/cron/:id/pause           暂停任务
POST /api/cron/:id/resume          恢复任务
POST /api/cron/:id/run             立即执行
GET  /api/logs?file=agent.log      日志内容
GET  /api/terminal/exec            执行命令
GET  /api/releases                 GitHub更新日志
```

---

## 12. 已知坑（来自原文档）

1. **配额计算**：不依赖数据库 `estimated_cost_usd` 零值，用模型定价表实时算
2. **移动端**：iOS Safari 双 overflow 嵌套导致内容截断——滚动只在一层
3. **配色**：原 Morandi 废弃，改用暗色 Apple 风格
4. **前端端口**：必须用 server.py 做代理，不能用 `python -m http.server`
