# Boss直聘自动筛选脚本

> ## ⚠️ 严重警告：会被封号！
>
> **Boss直聘已能检测Playwright等自动化工具，使用本项目会导致Web端账号被临时封禁。**
>
> ![封号截图](docs/boss-ban-warning.jpeg)
>
> 封禁期间无法通过Web端登录，仅能使用APP或PC客户端。**请勿将本代码用于实际生产环境，仅作学习研究参考。**

## 功能

- Boss直聘 PC 客户端 RPA 自动化
- 根据本地 JD 配置自动筛选候选人
- 命中规则后直接点击 `打招呼`
- `dry_run` 安全演练模式

## 技术栈

- Python 3.12 + PyAutoGUI + PaddleOCR
- 规则筛选为主，LLM 为可选增强

## 项目结构

```
boss-auto/
├── config/          # 配置文件
├── src/             # 核心代码
│   ├── rpa_crawler.py    # PC客户端RPA爬虫
│   ├── script_runner.py  # 命令行主入口
│   ├── resume_filter.py  # 规则+LLM双重筛选
│   ├── messenger.py      # 已处理候选人去重记录
│   └── llm_client.py     # LLM客户端（可选）
├── docs/            # 文档和截图
└── start.sh         # 启动脚本
```

## 使用方式

1. 打开并登录 Boss 直聘 PC 客户端，切到推荐列表页面。
2. 首次运行前先准备 `config/config.yaml`。
3. 如果仓库里只有 `config/config.yaml.example`，双击 `launch.command` 会自动复制出 `config/config.yaml` 模板。
4. 先把 `config/config.yaml` 里的 `api_key`、`vision_api_key` 改成你自己的，再决定是否把 `rpa.dry_run` 设为 `true`。
5. 执行：

```bash
./start.sh
```

或双击：

```bash
launch.command
```

- `dry_run: true`：只做 OCR 识别和流程演练，不点击、不发送消息
- `dry_run: false`：执行真实点击和消息发送

这只能降低 Playwright/WebDriver 那类检测暴露面，不能保证不会被平台风控。
