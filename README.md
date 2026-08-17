# ftpserve —— 运行在 OpenWRT（x86 / RK3576）上的轻量 FTP 容器

基于 **Alpine + vsftpd** 的最小化 FTP 服务容器。Alpine 使用 musl libc，与 OpenWRT 同源，
在 x86 与 RK3576（arm64）等架构上兼容性最佳、资源占用低。

## 需求对照

| 需求 | 实现方式 |
|------|----------|
| 1. 挂载宿主机 `/vol1/1000/ftp` 到容器 `/ftp` 作为上传下载目录 | 卷挂载 `-v /vol1/1000/ftp:/ftp` |
| 2. 挂载 `/vol1/1000/docker/ftpserve` 作为配置目录并持久化 | 卷挂载 `-v /vol1/1000/docker/ftpserve:/etc/ftpserve`，`vsftpd.conf` 生成并存于此 |
| 3. 默认账户/密码/目录支持环境变量设置 | `FTP_USER` / `FTP_PASS` / `FTP_DIR` |
| 4. 支持环境变量添加更多用户，并为每个用户分配各自目录 | `FTP_USERS="ftpm5:abc123:/ftp/ftpm5;..."` |

## 目录结构

```
.
├── Dockerfile          # 多架构镜像定义（alpine:3.20 + vsftpd）
├── start.sh            # 入口脚本：读环境变量 → 建用户 → 生成配置 → 前台运行 vsftpd
├── docker-compose.yml  # 推荐用法，含两个挂载与完整环境变量
├── build.sh            # 构建助手（本机 / 交叉多架构）
└── README.md
```

## 环境变量

### 默认账户
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FTP_USER` | `ftp` | 默认 FTP 账户名 |
| `FTP_PASS` | `ftp` | 默认账户密码 |
| `FTP_DIR`  | `/ftp` | 默认账户根目录（即挂载点） |

### 额外用户
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FTP_USERS` | 空 | 多个用户用 `;` 分隔，每项 `name:pass:dir[:uid:gid]`。例如 `ftpm5:abc123:/ftp/ftpm5` |

> 注意：账户名、密码、目录中**不要包含** `:` 与 `;`。

### 服务器 / 被动模式
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FTP_PORT` | `21` | 控制端口（改此值需同步改端口映射） |
| `PASV_MIN_PORT` / `PASV_MAX_PORT` | `21000` / `21010` | 被动模式数据端口范围（须与端口映射一致） |
| `PASV_ADDRESS` | 空 | **强烈建议设置**为 OpenWRT 对客户端可达的 IP（局域网/公网），否则客户端列目录会卡住 |
| `FTP_UID` / `FTP_GID` | 自动探测 `/ftp` 属主 | 默认账户 UID/GID。留空时脚本自动取 `/ftp` 挂载目录的属主，以保证可写 |
| `FTP_CONF_REGEN` | `yes` | `yes`=每次按环境变量重新生成配置并写入持久化目录；`no`=复用已有 `vsftpd.conf`（保留手动修改） |

每个额外用户也支持在 `FTP_USERS` 里追加 `:uid:gid`，例如 `ftpm5:abc123:/ftp/ftpm5:1000:1000`，
使其目录权限与宿主机某个 UID 对齐。

## 快速开始

### 方式 A：docker-compose（推荐）
```sh
# 在 OpenWRT 上，先按需修改 docker-compose.yml 中的 PASV_ADDRESS 与 FTP_USERS
docker compose up -d --build
```

### 方式 B：docker run
```sh
docker run -d --name ftpserve \
  -p 21:21 -p 21000-21010:21000-21010 \
  -e FTP_USER=ftp -e FTP_PASS=ftp \
  -e FTP_USERS="ftpm5:abc123:/ftp/ftpm5;ftpm6:def456:/ftp/ftpm6" \
  -e PASV_ADDRESS=192.168.1.1 \
  -v /vol1/1000/ftp:/ftp \
  -v /vol1/1000/docker/ftpserve:/etc/ftpserve \
  ftpserve:latest
```

## 多架构构建（x86 / RK3576）

镜像基于 `alpine:3.20`，官方仓库同时提供 `amd64` 与 `arm64` 变体，因此：

- **在目标设备本机构建（最简单、最稳）**：把本目录拷到 OpenWRT 上直接构建，得到原生架构镜像。
  ```sh
  # x86 OpenWRT
  ./build.sh ftpserve:x86
  # RK3576 (arm64) OpenWRT
  ./build.sh ftpserve:rk3576
  ```
- **在 x86 开发机上交叉构建**（需 buildx + qemu）：
  ```sh
  docker buildx create --use
  docker run --privileged --rm tonistiigi/binfmt --install all
  ./build.sh myrepo/ftpserve:latest linux/amd64,linux/arm64 --push
  ```
  交叉构建双平台必须 `--push` 到镜像仓库（本地无法同时 load 多架构）；若只构建单平台用于本地，
  用 `./build.sh ftpserve:latest linux/arm64` 即可直接载入。

## OpenWRT 部署要点

1. **被动模式 / 防火墙**：FTP 被动模式需要客户端回连 `PASV_ADDRESS:PASV_MIN~MAX_PORT`。
   - 桥接网络：设置 `PASV_ADDRESS` = OpenWRT 对客户端可达 IP，并在 OpenWRT 防火墙放行 `21` 与 `21000-21010`。
   - 更省事：使用 `network_mode: host`（compose 中取消注释），无需端口映射，PASV 自动走主机 IP。
2. **权限 / UID 映射**：容器内的 FTP 用户是真实系统用户。默认账户的家目录即挂载点 `/ftp`，
   为了能写入，脚本会自动把它的 UID/GID 设为 `/ftp` 在宿主机上的属主；若你知道确切 UID（如 1000），
   显式设置 `FTP_UID` / `FTP_GID` 最稳妥。额外用户的子目录由容器创建并 chown，通常开箱即用。
3. **配置持久化**：`vsftpd.conf` 写入 `/etc/ftpserve`（宿主机 `/vol1/1000/docker/ftpserve`），
   容器重建后依然保留。默认每次按环境变量重新生成；如需保留手动修改，设 `FTP_CONF_REGEN=no`。

## 排错

- **能登录但列目录卡住 / 超时**：几乎都是 PASV 问题。确认 `PASV_ADDRESS` 正确、被动端口已映射且防火墙放行。
- **能登录但不能上传**：默认账户的家目录是挂载点，UID 未与宿主机目录属主对齐。检查 `FTP_UID/FTP_GID`
  或确认 `/vol1/1000/ftp` 属主；额外用户子目录异常则看 `docker logs ftpserve` 的报错。
- **无法 chroot root 用户**：vsftpd 拒绝把 root(uid 0) 用户 chroot。脚本已规避（探测到属主为 0 时回退到 1000）。
- **查看日志**：`docker logs -f ftpserve`。

## 安全建议

- 默认示例密码仅为演示，**生产环境务必修改** `FTP_PASS` 与各用户密码。
- 如仅需内网使用，`PASV_ADDRESS` 填局域网 IP 即可；如需公网，建议配合防火墙白名单或在前端加 TLS。

## 预构建镜像（GHCR）

本仓库的 GitHub Actions 会自动构建并推送多架构镜像到 GHCR，无需本机构建：

```sh
# x86 / RK3576 通用，Docker 会自动按架构拉取对应变体
docker pull ghcr.io/ghostyubin/ftpserve/ftpserve:latest
docker pull ghcr.io/ghostyubin/ftpserve/ftpadmin:latest
```

镜像同时支持 `linux/amd64`（x86）与 `linux/arm64`（RK3576 等 ARM64）双架构。
