# ftpserve - 轻量 FTP 容器（Alpine + vsftpd）
# 目标平台: linux/amd64 (x86 OpenWRT) / linux/arm64 (RK3576 OpenWRT)
# Alpine 使用 musl libc，与 OpenWRT 同源，在嵌入式/路由器上兼容性最佳
FROM alpine:3.20

# 安装 vsftpd（依赖 linux-pam，自动提供 /usr/lib/security/pam_unix.so）+ jq（解析 users.json）
# + shadow（提供 useradd，支持 -o 共享 UID，用于让所有 FTP 用户映射到数据属主 admin）
RUN apk add --no-cache vsftpd jq shadow \
 && mkdir -p /etc/ftpserve /ftp \
 && if [ ! -f /etc/pam.d/vsftpd ]; then \
      printf 'auth\trequired\tpam_unix.so\naccount\trequired\tpam_unix.so\n' > /etc/pam.d/vsftpd; \
    fi

COPY start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

# 控制端口 + 被动模式数据端口范围
EXPOSE 21 21000-21010

# 这两个目录由宿主机挂载，实现数据与配置持久化
VOLUME ["/ftp", "/etc/ftpserve"]

ENTRYPOINT ["/usr/local/bin/start.sh"]
