# COBOT MAGIC Workbench

**Yang Taige · Ye Hanzhi — Emerge Robotics**

HKU Faculty of Engineering · Tam Wing Fam Innovation Wing

[Project page](https://www.google.com/search?q=https://eddy-yang.github.io/cobot-magic-workbench/) · [User manual](https://www.google.com/search?q=https://eddy-yang.github.io/cobot-magic-workbench/manual.html) · [Windows SDK](https://www.google.com/search?q=https://github.com/EDDY-YANG/cobot-magic-workbench/releases/tag/v0.1.3) · [中文手册](https://www.google.com/search?q=docs/USER_GUIDE.zh.md)

A browser workspace linking a Windows operator PC, an RTX 4090 edge host and COBOT MAGIC: device status, teleoperation, recording, policy deployment, controller selection and experiment review.

[Watch the measured recording on the project site](https://www.google.com/search?q=https://eddy-yang.github.io/cobot-magic-workbench/replay.html).

[Release notes v0.1.3](https://www.google.com/search?q=docs/release-v0.1.3.md) · [中文版本说明](https://www.google.com/search?q=docs/release-v0.1.3.zh.md) · [Connection preview](https://www.google.com/search?q=https://eddy-yang.github.io/cobot-magic-workbench/interface/index.html%23connect)

## Try it

Download and extract `cobot-magic-sdk-windows-x64.zip`, then double-click **Start-Workbench.cmd**. Python and OpenSSH are included. The recorded demo runs offline. For live access, enter your own authorized SSH profile and the configured GPU-host management port. The client does not automatically start a model or move a robot.

## Connection Automation & Onboarding Optimization（本次优化更新说明）

为解决 4090 主机与 AgileX 机械臂直连时“重启/拔插易断连、新主机需手动配网口 IP 与 SSH 公钥”的痛点，本分支新增了一套受安全守卫约束的自动化连接与新机初始化方案（详见 [连接优化方案](https://www.google.com/search?q=docs/cobot_magic_connection_optimization_plan.md) 与 [机械臂免改 IP 配置指南](https://www.google.com/search?q=docs/robot-dhcp-setup.md)）：

* **Phase 1 · 机械臂端免改 IP（`tools/setup_robot_dhcp.sh`）**：在机械臂直连网口配置固定 IP 与轻量 `dnsmasq` DHCP（显式置空默认网关与 DNS，不抢占对端主机 Wi-Fi 外网），配合 Avahi mDNS，任意新电脑插上网线即可自动连通。

* **Phase 2 · 新主机一键初始化向导（`tools/setup_host_link.py`、`Setup-Host-Link.cmd`、`setup_host_link.sh`）**：在新电脑上自动检测直连网口连通性、生成本机 `ed25519` SSH 密钥对、引导首次写入机械臂公钥，并自动生成 `~/.ssh/config` 固定别名与本地忽略的 `server/installation.local.json`。

* **Phase 3 · 后端四阶段一键连接编排（`server/connect_orchestrator.py`、`server/main.py`）**：新增 `POST /platform/connect` 与 `GET /platform/connect/status` 接口，按“网口与 SSH 探测 → USB 物理核验 → 会话与源码绑定 → 代理 / Hub / TimeQP(18970) 校验”四阶段依次核对并拉通链路。

* **Phase 4 · 控制中心前端交互升级（`web/platform.mjs`、`web/platform.css`）**：在系统链路面板加入「⚡ 一键连接机械臂」**按钮与 4 阶段实时状态反馈卡片，并在运行条件面板中加入**双臂协同 / 单左臂 / 单右臂构型切换。

* **安全边界保持不变**：自动化连接绝不自动加载模型、不自动解除 `STOP` 锁存、不重放运动或录制指令、不静默覆盖未核验源码，也不降级写入机械臂本地盘。

快速预演与自检命令：

```
# 预演机械臂端 DHCP 配置与新主机初始化向导（不修改系统文件）
bash tools/setup_robot_dhcp.sh --iface eth0 --dry-run
python3 tools/setup_host_link.py --dry-run

# 运行全量单元测试与前端 UI 测试
python3 -B -m unittest discover -s tests -p "test_*.py" -v
node --test tests/test_ui*.mjs

```

## Source map

| Folder | Function | 
| ----- | ----- | 
| `client/` | Portable Windows launcher, private SSH transport and protocol-checked gateway | 
| `server/`, `web/` | GPU-host management service (including guarded `/platform/connect` orchestrator) and compact management UI | 
| `lab_stack/` | Installed operator interface, shared device agent, recording and deployment orchestration | 
| `controllers/realtime_v2/` | Verified V2 source snapshot including evidence and trajectory-coverage fixes | 
| `controllers/shared_runner/` | FP32/BF16 runner snapshot and trajectory processing | 
| `inference/` | FP32 supervisor and BF16 service/integration source | 
| `site/` | Bilingual project/manual, actual UI source preview and measured replay | 
| `tools/`, `tests/` | Packaging, provenance, host onboarding & robot DHCP setup scripts, page generation and offline checks | 

The November 19 async recording/flush change (`3e6bd5e`) is included byte-for-byte in `recording.py` and `recording_lifecycle.py`. The V2 source was matched against all 64 source hashes of release `cdb09c1`; public path substitutions are recorded separately. Snapshot metadata lives in `docs/`.

## Scope

This is a research release, not a complete new-robot installer. The Windows client is portable; device, controller and model services require installation-specific environments, SSH authorization, USB identity and current boot/source binding. No weights or private machine configuration are included. The current policy experiments use the right follower arm; DAgger tools are under development.

The demo uses a real 2026-10-24 recording (FP32, step 10000, Realtime-VLA V2). That run ended on an evidence-queue error. Native camera/state streams were retained; task success was not scored. MuJoCo displays measured J1–J6 kinematics, not contact physics. Gripper width is numerical because the model aperture mapping is not calibrated.

## Develop

See [development and publication](https://www.google.com/search?q=docs/development.md), [source contracts](https://www.google.com/search?q=docs/server-contract.md), and [AGENTS.md](AGENTS.md). Changes are committed and pushed with a reviewable diff. GitHub Pages publication does not activate robot software.

## License

Original code and documentation are distributed under [MIT](https://www.google.com/search?q=LICENSE). Demonstration video content, playback versions, covers, and frames have [separate viewing and reuse terms](https://www.google.com/search?q=site/assets/demo/LICENSE.txt). Earlier valid grants, including the published CC BY 4.0 grant, continue to apply. See [third-party notices](THIRD_PARTY_NOTICES.md).

The system diagram renders the four-arm COBOT MAGIC assembly on a light background. The browser reads the pinned URDF and meshes directly from the AgileX Robotics repository. The official upstream preview is the fallback.