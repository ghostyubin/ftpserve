#!/bin/sh
# ftpserve 入口脚本
# 设计目标：
#   1) 默认账户/密码/目录 通过环境变量设置 (FTP_USER/FTP_PASS/FTP_DIR)
#   2) 额外用户：(a) 通过 FTP_USERS 环境变量；或 (b) 通过配置目录下的 users.json
#      —— users.json 由 Web 管理后台(ftpadmin)写入，是用户管理的唯一真相源
#   3) 配置持久化到 CONFIG_DIR（默认 /etc/ftpserve，宿主机挂载点）
#   4) 多架构 (x86 / RK3576 arm64) 通用
set -e

CONFIG_DIR="${CONFIG_DIR:-/etc/ftpserve}"
CONF_FILE="$CONFIG_DIR/vsftpd.conf"
USERS_JSON="$CONFIG_DIR/users.json"

# ---------------- 服务器相关环境变量 ----------------
PASV_MIN_PORT="${PASV_MIN_PORT:-21000}"
PASV_MAX_PORT="${PASV_MAX_PORT:-21010}"
PASV_ADDRESS="${PASV_ADDRESS:-}"
FTP_PORT="${FTP_PORT:-21}"

# ---------------- 默认账户 ----------------
FTP_USER="${FTP_USER:-ftp}"
FTP_PASS="${FTP_PASS:-ftp}"
FTP_DIR="${FTP_DIR:-/ftp}"
FTP_UID="${FTP_UID:-}"
FTP_GID="${FTP_GID:-}"

echo "==> ftpserve 启动"
echo "    默认账户 : $FTP_USER"
echo "    默认目录 : $FTP_DIR"

# 虚拟用户使用的 shell（禁止登录系统）
SHELL_BIN=/sbin/nologin
if [ ! -x "$SHELL_BIN" ]; then SHELL_BIN=/bin/false; fi

# 确保 PAM 配置存在
if [ ! -f /etc/pam.d/vsftpd ]; then
  cat > /etc/pam.d/vsftpd <<'PAM'
auth    required   pam_unix.so
account required   pam_unix.so
PAM
fi

# 添加单个 FTP 用户（真实系统用户 + chroot 到家目录）
# 参数: 名称 密码 目录 UID GID 是否chown目录
# 注意: 使用 useradd（shadow 包）以支持 -o 共享 UID；
#       所有 FTP 用户统一映射到数据属主(admin)的 UID，从而可读 fnOS 网页上传的文件。
add_ftp_user() {
  NAME="$1"; PASS="$2"; FOLDER="$3"; UIDV="$4"; GIDV="$5"; CHOWN_DIR="$6"
  [ -z "$NAME" ] || [ -z "$PASS" ] && return
  [ -z "$FOLDER" ] && FOLDER="/ftp/$NAME"

  # 重启时删除旧用户，保证环境变量/JSON 始终为唯一真相来源
  if getent passwd "$NAME" >/dev/null 2>&1; then
    deluser "$NAME" >/dev/null 2>&1 || userdel "$NAME" >/dev/null 2>&1 || true
  fi

  # 处理组：指定 GID 则复用/创建该组；否则建一个与用户名同名的组
  if [ -n "$GIDV" ]; then
    if ! getent group "$GIDV" >/dev/null 2>&1; then
      addgroup -g "$GIDV" "$NAME" >/dev/null 2>&1 || groupadd -g "$GIDV" "$NAME" >/dev/null 2>&1 || true
    fi
    GRP=$(getent group "$GIDV" | cut -d: -f1)
    [ -z "$GRP" ] && GRP="$NAME"
  else
    GRP="$NAME"
    addgroup "$NAME" >/dev/null 2>&1 || true
  fi

  # useradd 支持 -o（允许共享 UID）；-M 不自动建家目录（稍后自行 mkdir + chown）
  useradd -u "$UIDV" -o -M -d "$FOLDER" -s "$SHELL_BIN" -g "$GRP" "$NAME" >/dev/null 2>&1
  # 设置密码
  echo "$NAME:$PASS" | chpasswd >/dev/null 2>&1

  mkdir -p "$FOLDER"
  # 默认账户的家目录就是挂载点 /ftp，不强行 chown（以免改变宿主机文件属主）
  if [ "$CHOWN_DIR" != "no" ]; then
    chown -R "$NAME:$GRP" "$FOLDER"
  fi
}

# 把 FTP_USERS 环境变量转换为 users.json（仅在 JSON 尚不存在时，作为初始种子）
ftp_users_to_json() {
  echo "[" > "$USERS_JSON"
  first=1
  IFS=';'
  for entry in $FTP_USERS; do
    [ -z "$entry" ] && continue
    NAME=$(echo "$entry"   | cut -d: -f1)
    PASS=$(echo "$entry"   | cut -d: -f2)
    FOLDER=$(echo "$entry" | cut -d: -f3)
    UIDV=$(echo "$entry"   | cut -d: -f4)
    GIDV=$(echo "$entry"   | cut -d: -f5)
    [ "$first" = 0 ] && echo "," >> "$USERS_JSON"
    first=0
    printf '  {"name":"%s","pass":"%s","dir":"%s"' "$NAME" "$PASS" "$FOLDER" >> "$USERS_JSON"
    [ -n "$UIDV" ] && printf ',"uid":"%s"' "$UIDV" >> "$USERS_JSON"
    [ -n "$GIDV" ] && printf ',"gid":"%s"' "$GIDV" >> "$USERS_JSON"
    printf '}' >> "$USERS_JSON"
  done
  unset IFS
  echo "" >> "$USERS_JSON"
  echo "]" >> "$USERS_JSON"
}

# ---------------- 默认账户 UID/GID ----------------
DEF_UID="$FTP_UID"
DEF_GID="$FTP_GID"
if [ -z "$DEF_UID" ]; then
  D=$(stat -c %u /ftp 2>/dev/null || echo 0)
  if [ "$D" = "0" ] || [ -z "$D" ]; then D=1000; fi
  DEF_UID="$D"
fi
if [ -z "$DEF_GID" ]; then
  D=$(stat -c %g /ftp 2>/dev/null || echo 0)
  if [ "$D" = "0" ] || [ -z "$D" ]; then D=1000; fi
  DEF_GID="$D"
fi

add_ftp_user "$FTP_USER" "$FTP_PASS" "$FTP_DIR" "$DEF_UID" "$DEF_GID" "no"

# ---------------- 额外用户 ----------------
mkdir -p "$CONFIG_DIR"
if [ ! -f "$USERS_JSON" ] && [ -n "$FTP_USERS" ]; then
  echo "    由 FTP_USERS 生成初始 users.json"
  ftp_users_to_json
fi

if [ -f "$USERS_JSON" ]; then
  echo "    从 users.json 加载额外用户"
  COUNT=$(jq 'length' "$USERS_JSON")
  i=0
  while [ "$i" -lt "$COUNT" ]; do
    NAME=$(jq -r ".[$i].name" "$USERS_JSON")
    PASS=$(jq -r ".[$i].pass // \"\"" "$USERS_JSON")
    FOLDER=$(jq -r ".[$i].dir // \"\"" "$USERS_JSON")
    UIDV=$(jq -r ".[$i].uid // empty" "$USERS_JSON")
    GIDV=$(jq -r ".[$i].gid // empty" "$USERS_JSON")
    # 未显式指定 UID 时，默认映射到数据属主(admin)，确保可读 fnOS 上传的文件；
    # GID 不默认（每个用户独立组，仅 UID 共享以保证对宿主机文件可读）
    if [ -z "$UIDV" ]; then UIDV="$DEF_UID"; fi
    add_ftp_user "$NAME" "$PASS" "$FOLDER" "$UIDV" "$GIDV" "yes"
    i=$((i+1))
  done
elif [ -n "$FTP_USERS" ]; then
  echo "    额外用户(环境变量) : $FTP_USERS"
  IFS=';'
  for entry in $FTP_USERS; do
    [ -z "$entry" ] && continue
    NAME=$(echo "$entry"   | cut -d: -f1)
    PASS=$(echo "$entry"   | cut -d: -f2)
    FOLDER=$(echo "$entry" | cut -d: -f3)
    UIDV=$(echo "$entry"   | cut -d: -f4)
    GIDV=$(echo "$entry"   | cut -d: -f5)
    if [ -z "$UIDV" ]; then UIDV="$DEF_UID"; fi
    add_ftp_user "$NAME" "$PASS" "$FOLDER" "$UIDV" "$GIDV" "yes"
  done
  unset IFS
fi

# ---------------- 生成并持久化配置 ----------------
if [ "${FTP_CONF_REGEN:-yes}" = "no" ] && [ -f "$CONF_FILE" ]; then
  echo "    复用已有配置 $CONF_FILE (FTP_CONF_REGEN=no)"
else
  {
    echo "# 由 ftpserve 入口脚本生成。可自由编辑；设 FTP_CONF_REGEN=no 可保留手动修改"
    echo "listen=YES"
    echo "listen_port=$FTP_PORT"
    echo "listen_ipv6=NO"
    echo "anonymous_enable=NO"
    echo "local_enable=YES"
    echo "write_enable=YES"
    echo "local_umask=022"
    echo "dirmessage_enable=YES"
    echo "xferlog_enable=YES"
    echo "connect_from_port_20=YES"
    echo "chroot_local_user=YES"
    echo "allow_writeable_chroot=YES"
    echo "pasv_enable=YES"
    echo "pasv_min_port=$PASV_MIN_PORT"
    echo "pasv_max_port=$PASV_MAX_PORT"
    if [ -n "$PASV_ADDRESS" ]; then
      echo "pasv_address=$PASV_ADDRESS"
    fi
    echo "seccomp_sandbox=NO"
    echo "background=NO"
    echo "pam_service_name=vsftpd"
    echo "ftpd_banner=Welcome to ftpserve."
  } > "$CONF_FILE"
fi

echo "==> 启动 vsftpd（前台）"
exec /usr/sbin/vsftpd "$CONF_FILE"
