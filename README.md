# 基于 MongoDB 的问卷系统：第一阶段提交材料

GitHub：[Robert4063/Contemporary-Data-Management-Systems_26_fall](https://github.com/Robert4063/Contemporary-Data-Management-Systems_26_fall)；代码分支：[project1](https://github.com/Robert4063/Contemporary-Data-Management-Systems_26_fall/tree/project1)。

本目录包含第一阶段的完整源码、技术文档、自动化测试、测试记录、项目完成报告和 AI 使用日志。以下步骤以 **Windows PowerShell** 为例；从本目录开始操作，不需要开发者电脑上的 `project1` 开发目录、既有虚拟环境或数据库备份。GitHub 链接用于标明仓库及分支，本文件不表示本地修改已经推送到远程。

## 1. 材料与代码结构

```text
第一阶段提交材料/
├─ README.md                         本文件：材料导航与从零运行步骤
├─ 01_代码/
│  ├─ README.md                      源码快速入口
│  ├─ .env.example                   环境配置示例，不含真实凭证
│  ├─ MongoDBSchema.json             MongoDB 集合类型校验结构
│  ├─ backend/                       后端：Python / FastAPI / PyMongo
│  │  ├─ requirements.txt            运行依赖
│  │  └─ app/
│  │     ├─ __init__.py
│  │     ├─ main.py                  应用入口、路由、权限与业务流程
│  │     ├─ config.py                读取 .env 与环境变量
│  │     ├─ db.py                    数据库连接、集合校验器、索引初始化
│  │     ├─ models.py                请求结构与问卷配置校验
│  │     ├─ security.py              密码哈希、登录会话与身份认证
│  │     ├─ logic.py                 答案校验、条件匹配与路径重放
│  │     └─ statistics.py            整卷与单题统计
│  └─ frontend/                      前端：原生 HTML / CSS / JavaScript
│     ├─ index.html                  页面入口
│     ├─ app.js                      登录、编辑、填写、发布与统计交互
│     └─ style.css                   页面样式
├─ 02_文档/
│  ├─ 系统说明.md                    架构、模块分工与设计取舍
│  ├─ 数据库设计.md                  集合字段、嵌入/引用、索引与理由
│  ├─ API说明.md                     请求、响应、认证及错误约定
│  └─ 关键逻辑说明.md                校验、跳转、权限、时间与提交逻辑
├─ 03_测试用例/
│  ├─ pytest.ini                     测试发现与后端导入路径
│  ├─ requirements-test.txt          测试依赖，引用后端依赖清单
│  ├─ 测试用例与结果.md              输入、预期输出与实际结果
│  └─ tests/
│     ├─ __init__.py
│     ├─ conftest.py                 隔离数据库、测试夹具与结果记录
│     ├─ test_system.py              系统接口、权限、提交与统计
│     ├─ test_improvements.py        截止、幂等、发布及改进回归
│     ├─ test_logic.py               校验与跳转规则边界
│     └─ test_browser.py             Playwright 真实浏览器交互
└─ 04_项目完成报告/
   ├─ 2026_ECNU_PJ1_1_杨家辉.pdf     项目完成报告与 AI 使用说明
   └─ AI使用日志.md                  实际提示词、结果、修改及 AI 不足
```

技术文档提供实现细节，项目报告总结设计过程与结果，两者分别保留。运行后产生的 `.venv`、`.env`、数据库文件和测试输出不是缺失的提交文件，应在使用者电脑上生成。请保留四个编号目录的相对位置，测试会据此寻找后端源码。

## 2. 准备环境

需要 Python **3.11 或更高版本**、MongoDB Community Server，以及浏览器。完整自动化测试另外需要 Google Chrome；只启动应用不需要安装测试依赖、Node.js 或 npm。

可从 [Python 官网](https://www.python.org/downloads/)、[MongoDB Community Server](https://www.mongodb.com/try/download/community) 和 [Chrome 官网](https://www.google.com/chrome/) 获取安装程序。Python 安装时启用 PATH；MongoDB 可以作为 Windows 服务运行，也可以手动启动。验证命令：

```powershell
python --version
python -m pip --version
mongod --version
```

如果 `mongod` 没有加入 PATH，可以使用其实际路径，例如 `& 'D:\MongoDB\server\8.3\bin\mongod.exe' --version`，路径应按本机安装位置调整。

## 3. 从零安装与配置

在文件管理器中打开“第一阶段提交材料”，在该目录打开 PowerShell，然后执行：

```powershell
Set-Location './01_代码'
python -m venv .venv
& './.venv/Scripts/python.exe' -m pip install -r './backend/requirements.txt' --index-url https://pypi.org/simple
Copy-Item -LiteralPath './.env.example' -Destination './.env'
```

以上创建独立 Python 环境，直接调用其中的解释器，不需要激活脚本。`Copy-Item` 只在首次配置时执行，已有 `.env` 时请保留原配置。

打开 `01_代码/.env`，配置以下内容：

```dotenv
MONGO_URI=mongodb://127.0.0.1:27017
MONGO_DB=questionnaire_stage1
APP_SECRET=在这里替换为至少32个字符的随机密钥
SESSION_HOURS=24
```

可用下面的命令生成随机密钥，再将输出填入 `APP_SECRET`：

```powershell
& './.venv/Scripts/python.exe' -c "import secrets; print(secrets.token_hex(32))"
```

`MONGO_URI` 是数据库连接地址；`MONGO_DB` 是业务数据库名。`APP_SECRET` 应保持稳定，它参与匿名答卷的账号去重计算。使用已有数据时，继续使用原数据库连接、库名和密钥。环境变量优先于 `.env`，若结果与文件不一致，请检查终端是否设置过同名变量。

## 4. 启动 MongoDB 和应用

**如果 MongoDB 已经在 `127.0.0.1:27017` 运行，直接使用它，无须再启动一个。** 如果尚未运行，在 `01_代码` 目录打开单独的 PowerShell 窗口，执行：

```powershell
New-Item -ItemType Directory -Force './data'
mongod --bind_ip 127.0.0.1 --port 27017 --dbpath './data'
```

保持数据库窗口运行。在另一个 PowerShell 窗口进入 `01_代码`，启动应用：

```powershell
& './.venv/Scripts/python.exe' -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

浏览器打开：

- 应用页面：<http://127.0.0.1:8000/>
- 接口交互文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

FastAPI 同时提供前端页面和后端接口，**前端不用单独启动，也不要直接双击 `index.html`**。首次启动会连接 MongoDB，自动初始化集合校验器和索引，无须手动导入 `MongoDBSchema.json`。空数据库中没有预设账号或问卷，请先注册。业务数据保存于 `.env` 指定的数据库，重启应用后仍会保留。

停止时在应用窗口按 `Ctrl+C`；手动启动的 MongoDB 也在其窗口按 `Ctrl+C`。若采用 Windows 数据库服务，无须为停止应用而停止该服务。

## 5. 手工验证一条完整流程

1. 在首页注册并登录，点击创建问卷，填写标题、说明和未来的截止时间。创建时间由服务器记录；正式保存和发布必须填写合法截止时间。
2. 添加单选、多选、文本和数字题，设置必答、数量、长度或范围。若需要跳转，启用跳转开关并选目标；目标只能是后续题或结束。
3. 保存草稿后发布，或使用“直接发布”；确认框中确认后正式发布。复制生成的填写链接。
4. 用另一个账号打开链接并登录，按页面填写。输入不符合规则的答案，应看到具体错误；修正后提交。已有有效登录状态时无须重复登录。
5. 回到创建者账号，在工作台查看整卷及单题统计。检查选项次数、文本回答和数字平均值是否与提交内容一致。
6. 关闭问卷后再用填写链接进入：仍需先登录，可以查看只读题目和关闭提示、返回工作台，但不能新增答卷。自然截止后的行为相同。

默认每账号只能提交一次，创建者可在发布前允许多次填写；匿名模式也要求登录，只是不直接保存填写者 ID。发布后题目与规则冻结，标题和说明允许修改。示例地址 `127.0.0.1` 只能供当前电脑使用；跨网络分享需要另外部署可公网访问的服务并配置对应网址，材料不包含已经上线的公网服务。

## 6. 安装并运行自动化测试

测试需在启动环境之外安装测试依赖，以下从 `01_代码` 目录接着操作。测试会自行启动应用进程，无须保持第 4 节的业务应用运行。

```powershell
Set-Location '../03_测试用例'
& '../01_代码/.venv/Scripts/python.exe' -m pip install -r './requirements-test.txt' --index-url https://pypi.org/simple
$env:SURVEY_TEST_SCOPE = 'full'
& '../01_代码/.venv/Scripts/python.exe' -m pytest -v
```

默认从 PATH 查找 `mongod`，启动独立端口和数据目录的测试数据库。找不到时，在执行测试前设置实际程序路径：

```powershell
$env:MONGOD_BIN = 'D:\MongoDB\server\8.3\bin\mongod.exe'
```

如果希望复用已经运行的本机 MongoDB，可以改为设置下面的变量。测试使用随机命名的 `test_questionnaire_*` 数据库并在结束时清理，不使用 `.env` 中的业务库。

```powershell
$env:TEST_MONGO_URI = 'mongodb://127.0.0.1:27017'
```

浏览器测试默认使用已安装的 Google Chrome，不需要另行下载 Playwright 浏览器。非标准安装路径可以指定：

```powershell
$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
```

设置路径时以本机实际安装位置为准。如果只想先运行不含浏览器的测试：

```powershell
$env:SURVEY_TEST_SCOPE = 'api-and-logic'
& '../01_代码/.venv/Scripts/python.exe' -m pytest -v --ignore=tests/test_browser.py
```

运行会在 `03_测试用例/运行结果/` 自动生成 JSON 结果、缓存及必要日志；`test-results.json` 记录本次执行范围、输入、实际响应与通过情况，后一次执行会更新该文件。不要将局部测试结果当成完整回归。

提交中已有完整回归记录：**126 项通过、0 失败、0 跳过**，其中包含 16 项真实 Chrome 测试，另有 1 条依赖弃用警告。该轮于 `2026-10-22 05:32:34 UTC` 完成，耗时约 170.26 秒；逐例记录见 [测试用例与结果](03_测试用例/测试用例与结果.md)。本机实际重跑结果以终端和当次 JSON 为准。

另于 `2026-10-22 11:40:52 UTC` 完成从零运行核验：仅复制提交材料，在全新虚拟环境中按运行依赖清单从 PyPI 安装依赖，使用空的隔离 MongoDB 数据库启动。17 次 HTTP 检查全部通过，覆盖页面与静态资源、配置读取、集合自动初始化、注册登录、直接发布、配置跳转、非法数字拒绝、提交去重、统计和关闭后的只读访问。这次核验复用了本机已安装的 Python 3.13.2 与 MongoDB 可执行程序，不等同于在另一台全新电脑上实测。

在上述全新环境中继续安装测试依赖并重跑，最终于 `2026-10-22 12:42:10 UTC` 得到 **126 项通过、0 失败、0 跳过**，耗时 **140.63 秒**，有 1 条相同的依赖弃用警告。首轮曾出现 125 项通过、1 项在 Chrome 启动阶段退出；该例单独复查通过后，再次完整运行得到以上结果，没有修改业务代码。各轮分别记录，不累计通过数。

## 7. 数据与文档阅读入口

数据库使用四个集合：`users` 保存账号，`sessions` 保存登录会话，`surveys` 保存问卷及内嵌题目、校验和跳转配置，`responses` 独立保存每次合法提交。统计从答卷计算，不单独维护统计结果集合。

- [系统说明](02_文档/系统说明.md)：先了解模块如何配合。
- [数据库设计](02_文档/数据库设计.md)：查看字段、索引、嵌入与引用的取舍。
- [API 说明](02_文档/API说明.md)：查看接口及请求/响应格式。
- [关键逻辑说明](02_文档/关键逻辑说明.md)：查看规则执行和边界处理。
- [项目完成报告](04_项目完成报告/2026_ECNU_PJ1_1_杨家辉.pdf)：查看设计过程、结果分析与四项 AI 使用说明。
- [AI 使用日志](04_项目完成报告/AI使用日志.md)：查看真实提示词、生成结果与修改内容。

## 8. 常见启动问题

| 现象 | 检查或解决方法 |
| --- | --- |
| `No module named uvicorn` 或其他模块缺失 | 使用 `01_代码/.venv/Scripts/python.exe` 安装依赖和启动，避免混用系统 Python。 |
| 无法导入 `app`，或配置未生效 | 先进入 `01_代码` 再执行完整启动命令，保留 `--app-dir backend`，确认 `.env` 位于此目录。 |
| MongoDB 连接失败 | 检查数据库是否启动，以及 `.env` 中的地址、端口和认证信息；不要在占用的 27017 端口重复启动。 |
| 8000 端口被占用 | 将启动命令改成 `--port 8001`，并用对应端口访问页面。 |
| Chrome 测试启动失败 | 确认已安装 Chrome，必要时指定 `PLAYWRIGHT_CHROMIUM_EXECUTABLE`；未运行浏览器测试不能算完整通过。 |
| 截止后不能提交 | 这是预期行为。登录后仍能查看完整只读问卷并返回工作台。 |

本材料没有要求预先导入数据库内容或复制开发环境目录。验证依赖安装与应用启动需要本机已安装 Python、MongoDB，并能访问 Python 包源；公网部署、其他浏览器、真实手机、压力与容灾不属于已验证范围。
