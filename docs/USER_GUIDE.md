# 用户使用指南

## 一句话启动

```bash
cd cmcc-cloud-alive
python3 -m cmcc_cloud_alive
```

不要先学一堆英文子命令。普通用户直接运行上面这一条，程序会进入中文向导。

## 中文向导会做什么

1. 提示输入账号。
2. 提示输入密码；密码不显示是终端安全机制，不是卡住。
3. 登录后拉取你的真实云电脑列表。
4. 让你用数字选择云电脑；只要列表能显示，就允许选择。
5. 让你确认保活间隔和运行时间。
6. 正式开始任务后，只在最开始执行一次状态检测/开机逻辑：
   - 云电脑运行中：跳过开机，直接保活。
   - 云电脑关机/离线：自动开机一次，成功后立即保活。
   - 首次开机失败：直接终止，不进入保活。
7. 后续循环阶段：
   - 按间隔重复保活；
   - 每轮保活前不再检测开机、不再触发开机；
   - 每分钟打印一次状态，仅用于展示。

## 常用运行方式

永久运行：

```bash
python3 -m cmcc_cloud_alive
```

指定独立状态文件：

```bash
python3 -m cmcc_cloud_alive --state .runtime/cloud_pc.json
```

兼容入口：

```bash
python3 bin/cmcc_cloud_alive.py
```

## 日志和状态文件

- 状态文件默认由程序管理，也可以用 `--state` 指定。
- 交互保活会写同名 `.interactive.log` 日志。
- 不要把状态文件、日志、token 上传到 GitHub。

## 高级命令

只有需要调试时才看：

```bash
python3 -m cmcc_cloud_alive --help
python3 -m cmcc_cloud_alive interactive --help
```
