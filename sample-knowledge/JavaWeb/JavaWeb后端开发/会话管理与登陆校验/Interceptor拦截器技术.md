# Interceptor 拦截器技术

## 目录
- [什么是 Interceptor](#什么是-interceptor)
- [Interceptor 的工作原理](#interceptor-的工作原理)
- [Interceptor 的三个方法与生命周期](#interceptor-的三个方法与生命周期)
- [Interceptor 的使用步骤](#interceptor-的使用步骤)
- [代码示例：token 登录拦截器](#代码示例token-登录拦截器)
- [拦截路径的配置](#拦截路径的配置)
- [多个 Interceptor 的执行顺序](#多个-interceptor-的执行顺序)
- [Interceptor 与 Filter 的区别](#interceptor-与-filter-的区别)
- [常见应用场景](#常见应用场景)
- [小结](#小结)

---

## 什么是 Interceptor

> **Interceptor（拦截器）是 Spring MVC 框架提供的组件，用于在请求到达 Controller 之前、之后或完成渲染后，对请求进行拦截处理。**
> 
> 与 Filter过滤器技术 同属请求拦截体系，但 Interceptor 更靠近 Controller 层。

它与 Filter 类似，但属于 **更上层、更贴近 Spring 业务逻辑**的拦截机制。Filter 是 Jakarta Servlet 规范的一部分，而 Interceptor 是 **Spring 框架特有的**。

### 核心特点

- 属于 **Spring MVC 框架**，强依赖 Spring 容器
- 可以访问 **Spring 容器中的 Bean**（Service、Mapper、工具类等）
- 基于 **AOP 思想**实现，对 Controller 进行横切拦截
- 拦截时机更精细：Controller 前、Controller 后、页面渲染后各一次
- **只能拦截 Spring MVC 管理的请求**（即 Controller 方法）

### 在整个请求处理流程中的位置

```
┌─────────────────────────────────────────────────────────┐
│                       Tomcat 容器                        │
│   ┌─────────────────────────────────────────────────┐   │
│   │  Filter1 → Filter2（Servlet 规范，容器级别）       │   │
│   └─────────────────────────────────────────────────┘   │
│                            ↓                              │
│   ┌─────────────────────────────────────────────────┐   │
│   │         DispatcherServlet（前端控制器）            │   │
│   │   ┌─────────────────────────────────────────┐   │   │
│   │   │  Interceptor1 → Interceptor2 → ...      │   │   │
│   │   │          ↓ Controller                   │   │   │
│   │   └─────────────────────────────────────────┘   │   │
│   └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## Interceptor 的工作原理

### 三个关键方法

`HandlerInterceptor` 接口定义了三个方法，对应请求处理的三个不同阶段：

| 方法 | 调用时机 | 返回值 | 用途 |
|------|----------|--------|------|
| `preHandle()` | **Controller 之前** 执行 | `boolean` | 前置处理：登录校验、权限检查；`true` 放行，`false` 拦截 |
| `postHandle()` | **Controller 之后、页面渲染之前** | `void` | 后置处理：修改 ModelAndView、添加通用数据 |
| `afterCompletion()` | **页面渲染完成之后** | `void` | 清理工作：关闭流、记录日志、异常处理 |

### 接口定义（源码级）

```java
public interface HandlerInterceptor {

    default boolean preHandle(HttpServletRequest request,
                              HttpServletResponse response,
                              Object handler) throws Exception {
        return true;  // 默认放行
    }

    default void postHandle(HttpServletRequest request,
                            HttpServletResponse response,
                            Object handler,
                            @Nullable ModelAndView modelAndView) throws Exception {
    }

    default void afterCompletion(HttpServletRequest request,
                                 HttpServletResponse response,
                                 Object handler,
                                 @Nullable Exception ex) throws Exception {
    }
}
```

---

## Interceptor 的三个方法与生命周期

### `preHandle()` —— 最常用

**调用时机**：Controller 方法执行之前。**返回 `true` 放行，`false` 拦截**

```java
@Override
public boolean preHandle(HttpServletRequest request,
                         HttpServletResponse response,
                         Object handler) throws Exception {
    String uri = request.getRequestURI();
    if (uri.contains("/login")) {
        return true;
    }
    String token = request.getHeader("token");
    if (token == null || token.isEmpty()) {
        response.setStatus(401);
        return false;  // 拦截
    }
    try {
        JwtUtils.parseJwt(token);
        return true;  // 放行
    } catch (Exception e) {
        response.setStatus(401);
        return false;
    }
}
```

### `postHandle()` —— 较少用

**调用时机**：Controller 执行完成后，视图渲染之前。能拿到 `ModelAndView`

```java
@Override
public void postHandle(HttpServletRequest request,
                       HttpServletResponse response,
                       Object handler,
                       ModelAndView modelAndView) throws Exception {
    if (modelAndView != null) {
        modelAndView.addObject("now", LocalDateTime.now());
    }
}
```

### `afterCompletion()` —— 清理工作

**调用时机**：视图渲染完成后。能拿到 `Exception ex`

```java
private static final ThreadLocal<Long> START_TIME = new ThreadLocal<>();

@Override
public boolean preHandle(...) throws Exception {
    START_TIME.set(System.currentTimeMillis());
    return true;
}

@Override
public void afterCompletion(..., Exception ex) throws Exception {
    long cost = System.currentTimeMillis() - START_TIME.get();
    System.out.println("请求耗时：" + cost + "ms");
    START_TIME.remove();
}
```

---

## Interceptor 的使用步骤

### 步骤 1：创建拦截器类（实现 HandlerInterceptor + @Component）

```java
package luv.vansye.interceptor;

import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

@Component  // ← 交给 Spring 管理，才能被 WebConfig 注入
public class TokenInterceptor implements HandlerInterceptor {

    @Override
    public boolean preHandle(HttpServletRequest request,
                             HttpServletResponse response,
                             Object handler) throws Exception {
        // 业务逻辑...
        return true;
    }
}
```

### 步骤 2：在配置类中注册（@Configuration + WebMvcConfigurer）

```java
package luv.vansye.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration  // ← 标注为配置类
public class WebConfig implements WebMvcConfigurer {

    @Autowired
    private TokenInterceptor tokenInterceptor;

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(tokenInterceptor)
                .addPathPatterns("/**");  // 拦截所有请求
    }
}
```

---

## 代码示例：token 登录拦截器

### 结合你项目的实际代码 `tokenInterceptor.java`

```java
package luv.vansye.interceptor;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import luv.vansye.utils.JwtUtils;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

@Slf4j
@Component
public class tokenInterceptor implements HandlerInterceptor {

    @Override
    public boolean preHandle(HttpServletRequest request,
                             HttpServletResponse response,
                             Object handler) throws Exception {

        // 步骤1：获取请求路径
        String requestURI = request.getRequestURI();

        // 步骤2：判断是否是登录请求，是则放行
        if (requestURI.contains("/login")) {
            log.info("登录请求，放行");
            return true;
        }

        // 步骤3：从请求头获取 token
        // 前端约定：headers: { token: localStorage.getItem('token') }
        String token = request.getHeader("token");

        // 步骤4：判断 token 是否为空
        if (token == null || token.isEmpty()) {
            log.info("令牌为空，响应401");
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            return false;
        }

        // 步骤5：解析 token，验证合法性（抛异常 = 不合法）
        try {
            JwtUtils.parseJwt(token);
        } catch (Exception e) {
            log.info("令牌无效，响应401");
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            return false;
        }

        // 步骤6：校验通过，放行
        return true;
    }
}
```

### 对应的 `WebConfig.java`

```java
package luv.vansye.config;

import luv.vansye.interceptor.tokenInterceptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Autowired
    private tokenInterceptor tokenInterceptor;

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(tokenInterceptor)
                .addPathPatterns("/**")
                .excludePathPatterns(
                        "/login"
                );
    }
}
```

### 执行流程图

```
请求到达
   │
   ▼
 获取 requestURI
   │
   ▼
 包含 /login？ ──是──► return true 放行 ──► Controller
   │ 否
   ▼
 request.getHeader("token")
   │
   ▼
 token 为空？ ──是──► 401，return false
   │ 否
   ▼
 JwtUtils.parseJwt(token)
   │
   ├─ 成功 ──► return true 放行 ──► Controller
   │
   └─ 失败（抛异常）──► 401，return false
```

---

## 拦截路径的配置

### `addPathPatterns()` 配置要拦截的路径

```java
registry.addInterceptor(tokenInterceptor)
        .addPathPatterns("/**");           // 拦截所有请求（包括多级子路径）
        // .addPathPatterns("/emps/**")     // 只拦截 /emps 下的所有请求
        // .addPathPatterns("/login")       // 只拦截 /login 这一个路径
```

### `excludePathPatterns()` 配置不拦截的路径（白名单）

```java
registry.addInterceptor(tokenInterceptor)
        .addPathPatterns("/**")
        .excludePathPatterns(              // 排除以下路径
            "/login",                      // 登录接口
            "/static/**",                  // 静态资源
            "/favicon.ico"                 // 网站图标
        );
```

### 路径匹配规则（Ant 风格）

| 通配符 | 含义 | 示例 | 匹配路径 |
|--------|------|------|----------|
| `?` | 匹配任意**一个**字符 | `/emps/?` | `/emps/1`，**不匹配** `/emps/12` |
| `*` | 匹配任意**多个**字符（一层路径） | `/emps/*` | `/emps/list`，**不匹配** `/emps/list/detail` |
| `**` | 匹配任意**多层**路径 | `/emps/**` | `/emps/list`、`/emps/a/b/c` |

---

## 多个 Interceptor 的执行顺序

```
请求
  │
  ▼
 A.preHandle() → return true
  │
  ▼
 B.preHandle() → return true
  │
  ▼
 Controller 执行业务逻辑
  │
  ▼
 B.postHandle()   ← 先注册的后执行
  │
  ▼
 A.postHandle()
  │
  ▼
 视图渲染
  │
  ▼
 B.afterCompletion()
  │
  ▼
 A.afterCompletion()
  │
  ▼
响应返回
```

通过 `.order(int)` 显式指定：**数字越小越先执行**

```java
registry.addInterceptor(A).order(1);  // 先执行
registry.addInterceptor(B).order(2);  // 后执行
```

---

## Interceptor 与 Filter 的区别

| 对比项 | Filter（过滤器） | Interceptor（拦截器） |
|--------|-----------------|---------------------|
| **规范归属** | Jakarta Servlet 规范 | Spring MVC 特有 |
| **执行时机** | Servlet 之前（容器级别） | Controller 之前（Spring 级别） |
| **能否访问 Spring Bean** | ❌ 不可以 | ✅ 可以（@Autowired 注入） |
| **拦截范围** | 所有请求（含静态资源、JSP） | 仅 Controller 方法 |
| **依赖 Spring** | ❌ 不依赖 | ✅ 强依赖 |
| **配置方式** | `@WebFilter` + `@ServletComponentScan` | `@Configuration` + `WebMvcConfigurer` |
| **执行顺序** | 在 Interceptor **之前** | 在 Filter **之后** |

### 选择建议

| 场景 | 推荐使用 | 原因 |
|------|----------|------|
| 登录校验（需访问 Service） | **Interceptor** | 能注入 Spring Bean |
| 字符编码统一处理 | **Filter** | 需要最前端处理 |
| 请求日志记录 | 都可 | Interceptor 能拿到 Controller 信息 |
| 跨域 CORS | **Filter** | 容器级别统一设置 |

> **你的项目为什么选 Interceptor**：需要用 `JwtUtils` 和 `@Slf4j`，这些都是 Spring 管理的组件，Interceptor 可以直接访问。

---

## 常见应用场景

1. **登录验证**（你的项目正在使用）— 未登录请求拦截并返回 401
2. **权限检查** — 不同角色访问不同路径
3. **请求日志** — preHandle 记开始时间，afterCompletion 算耗时
4. **字符编码统一** — `request.setCharacterEncoding("UTF-8")`
5. **接口限流** — 统计某个 IP 的请求频率
6. **数据脱敏** — postHandle 中处理敏感字段

---

## 小结

### Interceptor 核心要点速记

| 要点 | 说明 |
|------|------|
| **接口** | `HandlerInterceptor` |
| **三个方法** | `preHandle()`（返回 boolean）、`postHandle()`、`afterCompletion()` |
| **放行控制** | `preHandle()` 返回 `true` 放行，`false` 拦截 |
| **交给 Spring** | 拦截器类加 `@Component` |
| **注册拦截器** | 配置类 `implements WebMvcConfigurer`，重写 `addInterceptors()` |
| **拦截路径** | `.addPathPatterns("/**")`；排除：`.excludePathPatterns("/login")` |
| **路径匹配** | `?` 单字符，`*` 单层，`**` 多层 |
| **执行顺序** | `.order()` 数字越小越先执行 |

### 代码模板

```java
// ─────── 拦截器类 ───────
@Component
public class XxxInterceptor implements HandlerInterceptor {
    @Override
    public boolean preHandle(...) throws Exception {
        return true;
    }
}

// ─────── 配置类 ───────
@Configuration
public class WebConfig implements WebMvcConfigurer {
    @Autowired
    private XxxInterceptor xxxInterceptor;

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(xxxInterceptor)
                .addPathPatterns("/**")
                .excludePathPatterns("/login");
    }
}
```

掌握 Interceptor 技术，你就能在 Spring 项目中优雅地实现登录校验、权限控制、日志记录等横切关注点！