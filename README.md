# Server Live Sync

[English](#english) | 中文

这是一个 Codex skill，用 Syncthing 把 Linux 服务器上的项目实时镜像到本地电脑。服务器端设为 `sendonly`，本地设为 `receiveonly`，适合在服务器开发、训练或跑实验，同时在本地查看代码和日志。

每个项目只需配置一次。以后服务器上新增、修改、重命名或删除文件，本地会自动跟进。电脑关机期间不会同步；重新开机并连接后会补齐变化。

## 安装

把下面这句话发给 Codex：

```text
请从 GitHub 安装 server-live-sync skill：
https://github.com/Takizzl/server-live-sync/tree/main/skill/server-live-sync
```

安装完成后，在下一轮对话中使用 `$server-live-sync`。

也可以通过 Codex 自带的 skill installer 安装：

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo Takizzl/server-live-sync \
  --path skill/server-live-sync
```

## 使用前准备

本地电脑需要：

- Python 3.9 或更高版本
- `ssh`、`scp` 和 `syncthing` 命令
- 能通过 SSH 登录目标服务器

服务器需要安装 Syncthing，并且待同步项目已经存在。脚本不会替你创建远端项目，也不会调用 `sudo`。

常见安装命令：

```powershell
# Windows
winget install Syncthing.Syncthing
```

```bash
# Ubuntu / Debian
sudo apt install syncthing

# macOS
brew install syncthing
```

## 让 Codex 配置同步

假设服务器项目是：

```text
gpu-box:/home/alice/projects/vla
```

### Windows 路径怎么写

`~` 表示当前用户的主目录，不代表任意磁盘上的代码目录。在 Windows 上：

- 用户名为 `Alice` 时，`~/code/vla` 通常是 `C:\Users\Alice\code\vla`
- 在你的电脑上，如果用户名是 `taki`，它通常是 `C:\Users\taki\code\vla`
- 如果希望放到 D 盘，必须明确写成 `D:\AProject\code\vla`

本地路径可以自由更换，本地文件夹名称也不必与服务器项目名一致。建议 Windows 用户直接提供完整路径。例如：

```text
使用 $server-live-sync，把 gpu-box 上 /home/alice/projects/vla
实时同步到本地 D:\AProject\code\vla
```

Codex 会先运行 dry-run，展示服务器路径、本地路径和同步方向。确认没有问题后，它会配置两端 Syncthing、配对设备并检查同步状态。

如果本地文件夹要换名字，例如保存为 `my-vla`：

```text
使用 $server-live-sync，把 gpu-box 上 /home/alice/projects/vla
同步到本地 D:\AProject\code\my-vla
```

在 Linux 或 macOS 上，也可以写成 `~/code/vla`；它会展开为当前用户主目录下的 `code/vla`。

## 直接运行脚本

通常交给 Codex 操作即可。排查问题时，可以手动运行：

```bash
# 检查本地、SSH 和服务器依赖
python scripts/live_sync.py doctor --ssh-host gpu-box

# Linux / macOS：只检查计划，不修改配置
python scripts/live_sync.py add \
  --ssh-host gpu-box \
  --remote-root /home/alice/projects \
  --project vla \
  --local-root ~/code \
  --dry-run
```

```powershell
# Windows：只检查计划，不修改配置
python scripts/live_sync.py add --ssh-host gpu-box --remote-root /home/alice/projects --project vla --local-root 'D:\AProject\code' --dry-run

# Windows：正式配置
python scripts/live_sync.py add --ssh-host gpu-box --remote-root /home/alice/projects --project vla --local-root 'D:\AProject\code'
```

脚本可以重复执行。已有配置匹配时，它会验证和修复状态，不会重复创建同步项。

## 默认不会同步的内容

默认排除以下内容：

- Git 内部数据、Python 缓存和编辑器缓存
- `node_modules`、`.venv` 和 `venv`
- `.pt`、`.pth`、`.ckpt`、`.safetensors`、`.onnx` 等模型文件
- 压缩包和常见视频文件

`.py`、`.json`、`.yaml`、`.sh`、`.md` 和 `.log` 等代码与日志文件会正常同步。项目已有 `.stignore` 时，skill 会保留原文件，不会覆盖。

## 安全设计

- 一次只配置一个明确项目，不镜像整个 home 目录。
- 服务器是唯一源端，本地不会反向上传修改。
- 不调用 Syncthing 的 `folder-override` 操作。
- 拒绝 `..` 路径穿越、宽泛目录和冲突配置。
- 不删除或改写服务器代码。

## 常见问题

### 本地关机后会丢文件吗？

不会。服务器继续记录变化，本地重新上线后会补同步。如果某个临时文件在本地离线期间创建后又被删除，本地不会看到这个短暂存在的文件。

### 为什么显示设备未连接？

先运行：

```bash
python scripts/live_sync.py doctor --ssh-host gpu-box
```

确认两端 Syncthing 正在运行，并检查防火墙是否允许 Syncthing 连接。即使无法直连，Syncthing 通常也可以通过中继连接。

### 能同步数据集和模型权重吗？

默认不行，这是为了避免意外下载几十 GB 的文件。确实需要时，复制 `scripts/default.stignore`，删掉对应规则，然后通过 `--ignore-file` 使用自定义文件。

## English

Server Live Sync is a Codex skill that mirrors one explicit project from an SSH-accessible Linux server to a local Windows, Linux, or macOS computer using Syncthing.

Install it by asking Codex:

```text
Install the server-live-sync skill from:
https://github.com/Takizzl/server-live-sync/tree/main/skill/server-live-sync
```

Then ask:

```text
Use $server-live-sync to mirror gpu-box:/home/alice/projects/vla to D:\Projects\vla.
```

On Windows, `~/code/vla` normally resolves to `C:\Users\<username>\code\vla`. It does not refer to a project folder on another drive. Supply an explicit absolute path such as `D:\Projects\vla` when that is the intended destination. The local folder name does not need to match the remote project name.

The server is configured as `sendonly` and the local folder as `receiveonly`. The skill checks prerequisites, runs a dry-run, pairs both Syncthing devices, configures the folder, and verifies its health. Python 3.9+, SSH, SCP, and Syncthing are required. No third-party Python packages are used.

## License

MIT
