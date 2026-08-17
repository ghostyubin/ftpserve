#!/bin/sh
# ftpserve 构建脚本
# 用法:
#   ./build.sh [镜像名] [平台]
#
# 示例:
#   在 RK3576 (arm64) 的 OpenWRT 本机构建:
#     ./build.sh ftpserve:rk3576
#   在 x86 的 OpenWRT 本机构建:
#     ./build.sh ftpserve:x86
#   交叉构建单个平台并载入本地:
#     ./build.sh ftpserve:latest linux/arm64
#   交叉构建双平台并推送到镜像仓库（需先 docker buildx create --use 且配置好 qemu/registry）:
#     ./build.sh myrepo/ftpserve:latest linux/amd64,linux/arm64 --push
set -e

IMAGE="${1:-ftpserve:latest}"
PLATFORM="${2:-}"
PUSHF=""
if [ "$3" = "--push" ]; then PUSHF="--push"; fi

if [ -n "$PLATFORM" ]; then
  echo "==> 交叉构建 $IMAGE 平台=$PLATFORM"
  docker buildx build --platform "$PLATFORM" -t "$IMAGE" $PUSHF .
else
  echo "==> 本机架构构建 $IMAGE"
  docker build -t "$IMAGE" .
fi
echo "==> 完成: $IMAGE"
