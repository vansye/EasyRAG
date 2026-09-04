---
type: knowledge
title: Docker 数据卷（Volume）
created: 2026-08-04
updated: 2026-08-04
tags: [Docker, 数据卷, 持久化, 存储]
source: 无
conclusion: 数据卷是 Docker 实现容器数据持久化与共享的核心机制，独立于容器生命周期，推荐使用 Volume 管理生产环境数据。
---

## 详细

### 概念
数据卷是 Docker 中用于持久化和共享容器数据的机制。它独立于容器的生命周期，容器删除后数据依然保留，并支持在多个容器之间共享数据。

### 重点
- **核心价值**：数据持久化（容器删除数据不丢）；数据共享（多个容器挂载同一卷）；性能优于容器存储层
- **三种挂载方式**：

  | 类型 | 管理方式 | 适用场景 |
  | :--- | :--- | :--- |
  | **Volume（卷）** | Docker 管理（`/var/lib/docker/volumes/`） | 生产环境首选，备份迁移方便 |
  | **Bind Mount（绑定挂载）** | 用户指定宿主机任意路径 | 开发热加载，需挂载源码目录 |
  | **tmpfs** | 内存存储，容器停止即清除 | 临时敏感数据，无需持久化 |
- **基本命令**：
  - `docker volume create mydata` 创建卷
  - `docker run -v mydata:/app/data nginx` 挂载卷（旧语法）
  - `docker run --mount source=mydata,target=/app/data nginx` 推荐语法
  - `docker volume ls` 列出所有卷
  - `docker volume inspect mydata` 查看卷详情
  - `docker volume rm mydata` 删除卷
- **最佳实践**：
  - 生产数据优先使用 **Volume**，由 Docker 管理生命周期
  - 开发调试使用 **Bind Mount** 实现代码热更新
  - 使用 `--mount` 语法（比 `-v` 更明确）
  - 定期清理无用卷（`docker volume prune`）
  - 备份卷：`docker run --rm -v mydata:/source -v $(pwd):/backup alpine tar czf /backup/backup.tar.gz -C /source .`