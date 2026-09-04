---
type: knowledge
title: Nginx 反向代理服务器
created: 2026-07-30
updated: 2026-07-30
tags: [Nginx, 反向代理, 负载均衡, 服务器, 部署]
source: 无
conclusion: Nginx 是一个高性能的 HTTP 和反向代理服务器，在前后端分离项目中主要用于解决跨域问题、负载均衡和静态资源托管，是部署 Vue/React 等单页应用的核心工具。
---

## 详细

### 概念
Nginx (发音 "engine x") 是一个轻量级、高性能的 Web 服务器和反向代理服务器。它通过异步非阻塞的事件驱动模型处理高并发请求，常用于部署静态资源、作为 API 网关分发请求，并提供负载均衡能力。

### 重点
- **反向代理**：代理服务器接收客户端的请求，转发给内部后端服务器（如 Spring Boot、Node.js），再将响应返回给客户端。用户无感知内网结构。
- **解决跨域**：通过在 Nginx 配置中统一转发 `/api` 前缀的请求到后端服务，使前后端同源，从而规避浏览器的跨域限制。
- **负载均衡**：支持轮询、加权轮询、IP Hash 等策略，将请求分发给多个后端实例，实现高可用和流量分摊。
- **动静分离**：将动态 API 请求转发给后端应用，将静态资源（HTML/CSS/JS）直接由 Nginx 返回，降低后端压力，提升加载速度。
- **核心配置示例**：
  ```nginx
  server {
      listen 80;
      server_name example.com;
      
      # 静态资源
      location / {
          root /usr/share/nginx/html/dist;
          try_files $uri $uri/ /index.html; # 解决 Vue Router 刷新404
      }

      # API 反向代理
      location /api/ {
          proxy_pass http://localhost:8080/;
          proxy_set_header Host $host;
          proxy_set_header X-Real-IP $remote_addr;
      }
  }