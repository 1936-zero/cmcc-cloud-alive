# 移动云电脑保活工具

这是一个给普通用户使用的移动云电脑保活工具。安装后按提示登录账号、选择云电脑、选择保活协议，就可以让程序定时发送保活流量，减少云电脑因为空闲而自动关机的情况。

> 你不需要会写代码，也不需要手动编辑 json 文件。大多数情况下，只要复制命令、按中文提示选择即可。

## 这个工具能做什么？

- 登录移动云电脑账号。
- 自动读取你的云电脑列表。
- 手动选择要保活的云电脑。
- 手动选择保活协议：`ZTE` 或 `SCG`。
- 云电脑未开机时，首次检测会自动开机一次。
- 云电脑已运行后，按你设置的间隔循环保活。
- token 失效时，会用已保存的账号密码自动重新登录并继续保活。
- 支持一个账号多台云电脑、多账号多开。

## 使用前需要准备什么？

你需要一台能联网的电脑，并安装：

- Git
- Python 3.10 或更高版本
- pip / venv

如果你不知道有没有安装，先按下面的系统步骤执行即可。

## 一键安装并启动

### Ubuntu / Debian / 云服务器

复制整段到终端执行：

```bash
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip \
&& git clone https://github.com/1936-zero/cmcc-cloud-alive.git \
&& cd cmcc-cloud-alive \
&& python3 -m venv .venv \
&& . .venv/bin/activate \
&& python3 -m pip install -U pip \
&& python3 -m pip install -e . \
&& python3 -m cmcc_cloud_alive
```

### macOS

先安装 Homebrew，然后执行：

```bash
brew install git python

git clone https://github.com/1936-zero/cmcc-cloud-alive.git
cd cmcc-cloud-alive
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -e .
python3 -m cmcc_cloud_alive
```

### Windows

使用 PowerShell 执行：

```powershell
git clone https://github.com/1936-zero/cmcc-cloud-alive.git
cd cmcc-cloud-alive
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
python -m cmcc_cloud_alive
```

如果 PowerShell 提示禁止运行脚本，先执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新执行：

```powershell
.\.venv\Scripts\Activate.ps1
python -m cmcc_cloud_alive
```

## 第一次怎么用？

启动后看到类似界面：

```text
移动云电脑保活工具
请输入命令：login 登录并开始保活；help 查看帮助；exit 退出。
cmcc>
```

输入：

```text
login
```

然后按中文提示操作：

1. 选择保活档案。
   - 第一次用，选择“新增一个账号/云桌面档案”。
   - 以后继续之前的云电脑，选择已有档案。
2. 输入移动云电脑账号。
3. 输入密码。
4. 程序自动读取云电脑列表。
5. 输入序号选择要保活的云电脑。
6. 手动选择协议：`ZTE` 或 `SCG`。
   - 你选 ZTE，程序就走 ZTE。
   - 你选 SCG，程序就走 SCG。
   - 程序不会偷偷替你自动切换协议。
7. 程序展示官方自动关机提示，例如：

```text
[官方自动关机时长]：<官方接口返回的提示文案>
```

8. 设置保活间隔、每轮保活持续秒数、运行轮数。
9. 程序开始保活。

如果当前云电脑没有开机，程序首次检测时会自动开机一次；后续循环只做保活和状态检测，不会反复开机。

## 以后每天怎么启动？

第一次安装完成后，以后不需要重复安装依赖。

### Linux / macOS

```bash
cd cmcc-cloud-alive
. .venv/bin/activate
python3 -m cmcc_cloud_alive
```

### Windows PowerShell

```powershell
cd cmcc-cloud-alive
.\.venv\Scripts\Activate.ps1
python -m cmcc_cloud_alive
```

进入程序后输入：

```text
login
```

选择之前保存的保活档案即可。

## 什么是保活档案？

保活档案就是程序为每台云电脑保存的一份本地记录，里面包含：

- 账号信息
- 云电脑选择
- token 缓存
- 协议选择
- 保活需要的状态信息

普通用户不需要打开或修改这些文件。

程序会自动保存到 `.runtime/` 目录。这个目录只在你的电脑本地使用，不应该发给别人，也不应该上传到 GitHub。

## token 失效了怎么办？

不用手动处理。

只要保活档案里保存的账号密码还是正确的：

- 启动时 token 失效，程序会自动重新登录。
- 保活运行中 token 失效，程序会在下一次检查/保活前自动刷新。
- 刷新成功后继续保活，不需要你删除 json，也不需要复制 token。

## 多账号 / 多台云电脑怎么多开？

一个终端窗口运行一个保活进程。

简单理解：

```text
一个终端窗口 = 一个保活进程
一个保活进程 = 一个保活档案
一个保活档案 = 一台云电脑的独立记录
```

### 多开第 1 台云电脑

打开第一个终端：

```bash
cd cmcc-cloud-alive
. .venv/bin/activate
python3 -m cmcc_cloud_alive
```

输入：

```text
login
```

选择“新增一个账号/云桌面档案”，然后登录并选择第 1 台云电脑。

### 多开第 2 台云电脑

不要关闭第一个终端。再打开第二个终端，执行同样命令：

```bash
cd cmcc-cloud-alive
. .venv/bin/activate
python3 -m cmcc_cloud_alive
```

输入：

```text
login
```

再次选择“新增一个账号/云桌面档案”，然后选择第 2 台云电脑。

可以是同一个账号下的不同云电脑，也可以是不同账号。

程序会自动生成独立档案，类似：

```text
.runtime/profiles/desktop1.json
.runtime/profiles/desktop2.json
.runtime/profiles/desktop3.json
```

你不需要手动创建这些文件。

## `.venv` 是什么？为什么要激活？

`.venv` 是这个工具专用的 Python 环境。

好处：

- 不污染系统 Python。
- 不影响电脑上的其他软件。
- 换电脑时步骤一致。
- 出问题更容易排查。

第一次安装时需要创建 `.venv` 并安装依赖。以后每次启动，只需要先激活 `.venv`，再运行程序。

## 常见问题

### 1. 我可以直接运行 `python3 -m cmcc_cloud_alive` 吗？

如果你已经在项目目录里，并且依赖已经安装，可以。

但推荐普通用户先激活 `.venv`，这样最稳定：

```bash
cd cmcc-cloud-alive
. .venv/bin/activate
python3 -m cmcc_cloud_alive
```

### 2. 选择已有档案后，还会重新问账号密码吗？

正常不会重复问。

你选择已有保活档案后，程序会直接使用该档案里的账号、密码、token 和云电脑信息。只有新增档案、档案损坏、没有保存密码、或者你主动选择重新输入时，才需要重新输入账号密码。

### 3. 保活期间会不会因为 token 失效直接退出？

正常不会。

程序会在关键步骤前检查 token，失效就自动重新登录并继续保活。

### 4. 官方自动关机时长是写死的吗？

不是。

程序展示的是官方接口返回的提示文案，格式类似：

```text
[官方自动关机时长]：<官方接口返回的提示文案>
```

这个内容只用于展示，不代表程序写死了某个分钟数。

### 5. 选错协议怎么办？

停止当前程序，重新启动后再次输入 `login`，选择对应档案，然后重新选择协议即可。

### 6. 想退出当前输入怎么办？

在输入提示里可以输入：

```text
exit
quit
q
```

程序会尽量返回主菜单。误按 `Ctrl-C` 或 `Ctrl-D` 时，也会尽量避免直接显示 Python 报错。

## 安全提醒

请不要把下面这些本地文件/目录发给别人：

```text
.runtime/
longtest_logs/
*.log
cloud_pc*.json
*_state.json
```

它们可能包含账号缓存、token、密码缓存或运行日志。

正常使用时，你只需要运行程序，不需要打开这些文件。

## 更新工具

如果以后仓库有更新，进入目录后执行：

```bash
cd cmcc-cloud-alive
git pull
. .venv/bin/activate
python3 -m pip install -e .
python3 -m cmcc_cloud_alive
```

Windows PowerShell：

```powershell
cd cmcc-cloud-alive
git pull
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m cmcc_cloud_alive
```
