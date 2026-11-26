#!/usr/bin/env bash
# Phase 1: Configure AgileX robot host direct-Ethernet port with static IP + dnsmasq DHCP
# (no default gateway or DNS pushed, so connected 4090 / laptops keep their Wi-Fi internet)
# and Avahi mDNS (agilex-robot.local).
#
# Usage on the AgileX Ubuntu host:
#   sudo bash tools/setup_robot_dhcp.sh --iface eth0 --robot-ip 192.168.10.2
set -euo pipefail

IFACE=""
ROBOT_IP="192.168.10.2"
PREFIX="24"
DHCP_START="192.168.10.10"
DHCP_END="192.168.10.50"
CONN_NAME="agilex-direct-link"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --iface) IFACE="$2"; shift 2 ;;
    --robot-ip) ROBOT_IP="$2"; shift 2 ;;
    --dhcp-start) DHCP_START="$2"; shift 2 ;;
    --dhcp-end) DHCP_END="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      echo "Usage: sudo bash $0 --iface <eth_iface> [--robot-ip 192.168.10.2] [--dhcp-start 192.168.10.10] [--dhcp-end 192.168.10.50] [--dry-run]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$IFACE" ]]; then
  echo "Error: --iface is required (run 'ip -br link' on the robot host to find the direct Ethernet interface)." >&2
  exit 2
fi

DNSMASQ_CONF="/etc/dnsmasq.d/agilex-direct-dhcp.conf"
DNSMASQ_CONTENT="# Managed by cobot-magic-workbench tools/setup_robot_dhcp.sh
interface=${IFACE}
bind-interfaces
dhcp-range=${DHCP_START},${DHCP_END},255.255.255.0,12h
# Do NOT push default gateway (option 3) or DNS (option 6) so the 4090/PC keeps Wi-Fi internet.
dhcp-option=3
dhcp-option=6
"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY-RUN] Would configure interface=${IFACE} static=${ROBOT_IP}/${PREFIX} (${CONN_NAME})"
  echo "[DRY-RUN] Would write ${DNSMASQ_CONF}:"
  printf "%s\n" "$DNSMASQ_CONTENT"
  echo "[DRY-RUN] Would restart dnsmasq and avahi-daemon"
  exit 0
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "Please run as root (sudo bash $0 ...)." >&2
  exit 1
fi

echo "[1/4] Configuring static IP ${ROBOT_IP}/${PREFIX} on ${IFACE} via NetworkManager..."
if command -v nmcli >/dev/null 2>&1; then
  nmcli connection delete "${CONN_NAME}" >/dev/null 2>&1 || true
  nmcli connection add type ethernet ifname "${IFACE}" con-name "${CONN_NAME}" \
    ipv4.method manual ipv4.addresses "${ROBOT_IP}/${PREFIX}" ipv4.never-default yes \
    autoconnect yes
  nmcli connection up "${CONN_NAME}"
else
  echo "Warning: nmcli not found; configure ${ROBOT_IP}/${PREFIX} on ${IFACE} via netplan manually." >&2
fi

echo "[2/4] Writing ${DNSMASQ_CONF}..."
mkdir -p /etc/dnsmasq.d
printf "%s" "$DNSMASQ_CONTENT" > "${DNSMASQ_CONF}"

echo "[3/4] Restarting dnsmasq..."
if command -v dnsmasq >/dev/null 2>&1; then
  systemctl enable dnsmasq >/dev/null 2>&1 || true
  systemctl restart dnsmasq
else
  echo "Note: dnsmasq is not installed. Run 'sudo apt-get install -y dnsmasq' then re-run this script." >&2
fi

echo "[4/4] Ensuring Avahi mDNS is active..."
if systemctl list-unit-files | grep -q avahi-daemon; then
  systemctl enable avahi-daemon >/dev/null 2>&1 || true
  systemctl restart avahi-daemon
fi

echo "Done! Direct-Ethernet clients plugged into ${IFACE} will auto-receive ${DHCP_START}-${DHCP_END} and reach the robot at ${ROBOT_IP}."
