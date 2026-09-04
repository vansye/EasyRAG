---
type: knowledge
title: Docker 镜像
created: 2026-08-04
updated: 2026-08-04
tags: [Docker, 镜像, 容器, DevOps]
source: 无
conclusion: Docker 镜像是用于创建容器的只读模板，基于 UnionFS 分层构建，是容器化应用打包和分发的基础单元。
---

## 详细

### 概念
Docker 镜像是一个轻量级、只读的独立软件包，包含运行应用程序所需的所有内容（代码、运行时、系统工具、库和设置）。它采用分层文件系统（UnionFS）构建，每一层代表 Dockerfile 中的一条指令，层与层之间共享和复用，从而节省存储空间并加速构建与分发。

### 重点
- **核心特性**：只读模板，用于创建容器；采用分层存储结构；通过镜像仓库实现共享与分发。
- **Dockerfile**：用于定义镜像构建过程的文本文件，每条指令生成一个新层（如 `FROM`、`RUN`、`COPY`、`CMD`）。
- **分层优势**：层可复用（如多个镜像共用基础层）；构建时利用缓存加速；拉取时只下载缺失层。
- **常用命令**：
  - `docker build -t myapp:1.0 .`：构建镜像
  - `docker images`：列出本地镜像
  - `docker pull nginx:latest`：从仓库拉取镜像
  - `docker push username/myapp:1.0`：推送镜像到仓库
  - `docker rmi <image-id>`：删除本地镜像
  - `docker history <image>`：查看镜像历史层
- **镜像命名**：`[registry/][namespace/]repository:tag`（如 `docker.io/library/nginx:alpine`），缺省时默认使用 Docker Hub 官方仓库。
- **最佳实践**：使用轻量级基础镜像（如 Alpine）减小体积；利用构建缓存合并 RUN 指令；尽量使用多阶段构建减小最终镜像体积；避免将敏感信息写入镜像层。