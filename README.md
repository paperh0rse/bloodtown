# 血染钟楼 - 自动讲述者

Blood on the Clocktower 联机 Web 应用，程序自动担任讲述者。

## 功能

- 支持 5-15 人联机游戏
- 剧本：暗流涌动 (Trouble Brewing)，包含全部 22 个角色
- 角色从角色池中**随机抽取**，每局游戏角色组合不同
- 开局自动洗牌，随机分配座位
- 每天随机指定发言起始位置和顺逆时针方向
- 程序自动执行讲述者职责：夜晚唤醒、信息传递、投票计票、胜负判定
- 第一夜自动进行爪牙/恶魔相认
- 每个玩家有独立的私密页面，夜间操作互不可见
- 实时 WebSocket 通信，支持断线重连
- 可添加 Bot 方便测试
- 游戏结束后可查看复盘总结

## UI 特性

- 三栏布局：游戏日志/聊天 | 镇广场/笔记本 | 角色信息/提名投票
- 纯 CSS 响应式适配，1080p / 2K / 4K 屏幕自动缩放（基于 `clamp()` + 视口单位）
- 圆桌式镇广场，座位旁可标注职业/阵营猜测（按颜色分类，支持自定义标签）
- 死亡玩家区分是否还有投票权（实线 vs 虚线）
- 广播信息栏实时显示阶段切换、死亡、发言顺序等
- 帮助弹窗包含角色介绍和不同人数的阵营分配表
- 玩家座位号以金色标签显示，在日志、广播、提名记录中统一醒目标识
- 提名/开枪等操作统一使用游戏内确认弹窗
- 首次访问自动弹窗设置昵称，后续进出房间无需重复输入

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 安装 cloudflared（公网访问必需）

```bash
winget install Cloudflare.cloudflared
```

### 3. 启动服务器

```bash
python run.py
```

启动后程序会自动创建公网隧道，终端显示公网链接：

```
==========================================================
  Blood on the Clocktower  |  Auto-Storyteller
==========================================================

  正在创建公网隧道 (cloudflared)...

============================================================
  PUBLIC URL: https://xxx-xxx.trycloudflare.com
============================================================
```

把这个链接发给朋友，用浏览器打开即可。**免费、无需注册、无需配置。**

> 隧道断开后会自动重连（链接会变化）。如需固定域名，可配置 cloudflared Named Tunnel。

### 4. 游戏流程

1. 首次访问设置昵称（之后自动记住）
2. 一名玩家点击「创建房间」
3. 把房间码或链接发给朋友，朋友加入房间
4. 房主点击「开始游戏」（需 5-15 人）
5. 程序自动洗牌分配座位和角色，开始第一个夜晚
6. **白天讨论请使用外部语音**（Discord / 微信语音 / 腾讯会议等）
7. 在网页上进行提名、投票和使用角色能力
8. 游戏进行中任何玩家可发起重开投票，所有人同意后重开
9. 游戏结束后可查看复盘总结，房主可重新开始

## 命令行参数

```bash
python run.py                  # 默认端口 8000，自动创建公网隧道
python run.py --port 9000      # 使用 9000 端口
python run.py --no-tunnel      # 不启动公网隧道（仅局域网）
```

## 运行测试

```bash
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

测试覆盖（121 个用例）：胜负条件判定、角色技能逻辑（杀手/小恶魔/僧侣/共情者/厨师/占卜师/投毒者/管家/猩红女郎接替等）、投票机制（管家限制/死人票/票数阈值/重开投票）、夜晚死亡结算。

## 技术栈

- 后端：Python 3.11+ / FastAPI / WebSocket
- 前端：HTML5 + CSS3 + Vanilla JS
- 公网访问：cloudflared 隧道（断线自动重连）
- 测试：pytest + pytest-asyncio
- 数据：内存存储（无需数据库）

## 项目结构

```
bloodtown/
├── run.py                # 启动入口（含公网隧道，断线自动重连）
├── requirements.txt      # Python 依赖
├── pytest.ini            # 测试配置
├── server/
│   ├── main.py           # FastAPI 路由 + WebSocket
│   ├── game_engine.py    # 游戏引擎（状态机 + 所有逻辑）
│   ├── models.py         # 数据模型 + 人数分配表
│   ├── role_data.py      # 角色定义
│   └── ws_manager.py     # WebSocket 管理
├── static/
│   ├── index.html        # 大厅页面（取名 + 创建/加入房间）
│   ├── game.html         # 游戏页面
│   ├── css/style.css     # 暗色哥特风格主题
│   └── js/
│       ├── ws.js         # WebSocket 封装（自动重连）
│       ├── app.js        # 大厅逻辑（昵称管理 + 房间列表）
│       └── game.js       # 游戏交互（镇广场 + 技能 + 投票）
└── tests/
    ├── conftest.py       # 测试工具（FakeManager + make_game 辅助函数）
    ├── test_win_conditions.py   # 胜负条件测试
    ├── test_roles.py            # 角色技能测试
    ├── test_voting.py           # 投票机制测试（含重开投票）
    ├── test_night_deaths.py     # 夜晚结算测试
    └── test_smoke.py            # 端到端冒烟测试
```
