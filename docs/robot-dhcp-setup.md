# 机械臂端直连网口免改 IP 配置指南（Phase 1）

在 AgileX 机械臂主机上完成一次配置后，以后无论更换哪台 4090 主机或笔记本、插在电脑的哪个网口上，只要保持系统默认的“自动获取 IP（DHCP）”，插上网线 2 秒内即可自动连通机械臂，**无需手动修改电脑网口 IP**，且不会影响电脑原有的 Wi-Fi 上网。

## 一键配置步骤（在机械臂 Ubuntu 主机执行）

1. 查看机械臂连接网线的有线网口名称（例如 `eth0` 或 `enp2s0`）：
   ```bash
   ip -br link
   ```
2. 确认已安装 `dnsmasq`（如未安装，先连网安装一次）：
   ```bash
   sudo apt-get update && sudo apt-get install -y dnsmasq avahi-daemon
   ```
3. 先用 `--dry-run` 预览将要写入的配置：
   ```bash
   bash tools/setup_robot_dhcp.sh --iface eth0 --robot-ip 192.168.10.2 --dry-run
   ```
4. 正式应用配置：
   ```bash
   sudo bash tools/setup_robot_dhcp.sh --iface eth0 --robot-ip 192.168.10.2
   ```

## 为什么不会抢占 4090 / 笔记本的 Wi-Fi 外网？

脚本在 `/etc/dnsmasq.d/agilex-direct-dhcp.conf` 中显式置空了 `dhcp-option=3`（默认网关）和 `dhcp-option=6`（DNS），因此网线只负责 `192.168.10.0/24` 这一条机械臂直连局域网，所有外网流量仍继续走电脑自己的 Wi-Fi 或校园网网口。

## 验收方法

拿一台未改过静态 IP 的电脑直插机械臂网口，等待 2 秒后执行：

```bash
ping -c 3 192.168.10.2
```

若能稳定收到回包且本机浏览器仍可正常访问外网，即配置成功。
