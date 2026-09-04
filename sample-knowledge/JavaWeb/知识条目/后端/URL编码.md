---
type: knowledge
title: URL 编码
created: 2026-07-24T00:00:00
updated: 2026-08-30T00:00:00
tags: [JavaWeb, 编码, 入门]
source:
conclusion:
---

## 详细

### 概念

URL 编码用于把特殊字符转换成浏览器和服务器都能正确识别的格式，尤其是中文、空格、&、= 等字符。JavaWeb 中常在 [[HTTP请求]] 的参数、路径和表单提交时遇到。如果不做编码，参数可能会被错误拆分或乱码。


### 重点

- URL 中不能直接安全传递所有字符
- 中文参数常需要编码和解码
- 常见处理类是 java.net.URLEncoder 和 URLDecoder