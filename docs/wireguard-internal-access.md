# WireGuard internal access

The unified runtime uses host-level WireGuard to reach maintainer-only services without publishing
their interfaces on public HTTPS endpoints.

## Runtime contract

Public ingress consists of `80/tcp`, `443/tcp`, and one chosen WireGuard UDP port. nginx binds the
private interfaces only to `VPN_BIND_ADDRESS`:

- MinIO Console: `http://<VPN_BIND_ADDRESS>:18081`
- Databasus: `http://<VPN_BIND_ADDRESS>:18082`

Set production `VPN_BIND_ADDRESS` to the server address on `wg0`, for example `10.77.0.1`.
Development keeps it on `127.0.0.1`. PostgreSQL, Valkey, application processes, MinIO, and
Databasus do not publish their own ports; nginx is the only normal runtime service with host port
mappings.

## Host setup

Install WireGuard on the production host:

```bash
sudo apt update
sudo apt install wireguard
```

Generate keys outside the repository and keep private keys out of logs:

```bash
umask 077
wg genkey | tee server.private | wg pubkey > server.public
wg genkey | tee maintainer-laptop.private | wg pubkey > maintainer-laptop.public
```

Create `/etc/wireguard/wg0.conf` on the server:

```ini
[Interface]
Address = 10.77.0.1/24
ListenPort = 51820
PrivateKey = <server private key>
SaveConfig = false

[Peer]
PublicKey = <maintainer laptop public key>
AllowedIPs = 10.77.0.2/32
```

Protect and enable it:

```bash
sudo chown root:root /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
sudo wg show
```

Use a maintainer client configuration whose `AllowedIPs` contains only the server VPN address:

```ini
[Interface]
Address = 10.77.0.2/32
PrivateKey = <maintainer laptop private key>

[Peer]
PublicKey = <server public key>
Endpoint = <server public IP or domain>:51820
AllowedIPs = 10.77.0.1/32
PersistentKeepalive = 25
```

Do not route the full client internet connection, Docker subnet, PostgreSQL, or Valkey through this
VPN without a separately reviewed network design.

## Firewall baseline

Keep an SSH rule matching the current server access policy before enabling a restrictive firewall.
For UFW, adapt the addresses and WireGuard port:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <trusted admin IP> to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 51820/udp
sudo ufw allow in on wg0 to 10.77.0.1 port 18081 proto tcp
sudo ufw allow in on wg0 to 10.77.0.1 port 18082 proto tcp
sudo ufw enable
sudo ufw status verbose
```

Keep `18081` and `18082` closed on the public interface. Docker adds host NAT/firewall rules;
verify from a network without WireGuard that these ports cannot connect on the public address.
If they can, add explicit public-interface drops before Docker's accept path through host-managed
UFW or `DOCKER-USER` policy.

## Deployment and verification

Set `VPN_BIND_ADDRESS` in `config/platform/production.env` before running the manual shared
infrastructure deployment workflow. After deployment, inspect host bindings:

```bash
docker compose ps nginx
sudo ss -lntp | grep -E ':(80|443|18081|18082)\b'
```

Expected result:

- `80` and `443` are reachable on the public address.
- `18081` and `18082` bind only to `VPN_BIND_ADDRESS`.

From a public network, all private ports must fail to connect:

```bash
curl --connect-timeout 3 http://<server public IP>:18081
curl --connect-timeout 3 http://<server public IP>:18082
```

From the maintainer device on WireGuard, the panels should reach their login surfaces:

```bash
curl -I http://10.77.0.1:18081
curl -I http://10.77.0.1:18082
```

## Revocation

To revoke a maintainer device, remove its peer from `/etc/wireguard/wg0.conf` and the live
interface:

```bash
sudo wg set wg0 peer <revoked peer public key> remove
sudo wg show
```

## References

- [WireGuard Quick Start](https://www.wireguard.com/quickstart/)
- [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)
- [UFW documentation](https://help.ubuntu.com/community/UFW)
