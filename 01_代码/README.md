# 运行与配置说明

完整的材料导航、代码结构、从零安装与测试步骤见[提交材料根目录 README](../README.md)。本文件保留源码目录的快速启动入口。

本目录为完整运行代码：frontend/是前端，backend/app/是后端，MongoDBSchema.json为集合类型校验结构；索引在backend/app/db.py启动时自动建立。前后端由同一个FastAPI服务提供，无需npm。

## 启动

安装Python 3.11+和MongoDB Server，在本目录打开PowerShell：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
```

编辑.env：MONGO_URI为MongoDB地址，MONGO_DB为业务库名，SESSION_HOURS为登录小时数；将APP_SECRET替换为至少32字符的随机字符串，并保持稳定。已有.env时保留原密钥，不覆盖。

若已有27017端口MongoDB服务，直接复用。否则在本目录的一个终端运行：

```powershell
New-Item -ItemType Directory -Force data
mongod --bind_ip 127.0.0.1 --port 27017 --dbpath ./data
```

mongod未加入PATH时，使用实际mongod.exe绝对路径启动。在另一个终端进入本目录，运行：

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

访问http://127.0.0.1:8000/；健康检查http://127.0.0.1:8000/api/health。停止用Ctrl+C。数据库初始为空，自行注册；不要双击HTML。127.0.0.1仅本机访问，未部署公网服务。

## 测试

需要MongoDB Server与Chrome。若未加入PATH，可设置MONGOD_BIN为mongod.exe路径；自定义Chrome可设置PLAYWRIGHT_CHROMIUM_EXECUTABLE。使用独立测试库，不清空业务库。

从本目录进入相邻的03_测试用例目录：

```powershell
Set-Location ../03_测试用例
../01_代码/.venv/Scripts/python.exe -m pip install -r requirements-test.txt
$env:SURVEY_TEST_SCOPE = 'full'
../01_代码/.venv/Scripts/python.exe -m pytest -v
```

运行结果在03_测试用例/运行结果中生成；提交材料不包含这些重复的原始日志与缓存。现有逐例输入、预期、实际输出和结果见[测试用例与结果](../03_测试用例/测试用例与结果.md)。
