# 🌐 Web 爬虫反封禁技术知识库

> 整理自 Bright CN Blog (2026-07)
> 来源: [Anti-Scraping Techniques](https://www.bright.cn/blog/web-data/anti-scraping-techniques) · [Web Scraping Without Getting Blocked](https://www.bright.cn/blog/web-data/web-scraping-without-getting-blocked)

---

## 一、背景：机器人已占全网 51% 流量

Cloudflare、Akamai、DataDome 等反机器人系统在提供 HTML 之前就进行多层检测封锁。单点绕过（如仅轮换 IP）已不足以应对现代反爬系统。

---

## 二、网站封锁爬虫的五大检测层

| 检测层 | 工作原理 |
|--------|---------|
| **IP 检测** | 维护数据中心 IP 段黑名单（AWS、GCP 等），检测单 IP 请求量过高 |
| **TLS 指纹识别** | 对 ClientHello 阶段的密码套件、扩展等哈希成 JA3/JA4 指纹，匹配已知机器人签名 |
| **浏览器指纹识别** | 检测 `navigator.webdriver`、canvas 渲染、WebGL GPU 字符串、字体、屏幕分辨率等数十种信号 |
| **行为分析** | 测量请求间隔、滚动模式、鼠标轨迹、导航深度等整个会话模式，使用 ML 区分人机 |
| **蜜罐陷阱** | 隐藏链接（`display:none`）对用户不可见，但对原始 HTML 爬虫可见，访问即标记 |

---

## 三、7+12 种核心技术方法（综合版）

### 1. 🔄 使用代理轮换 IP 地址

将流量分散到多个 IP，防止单个 IP 积累可疑请求量。自动重试被封锁的 IP。

### 2. 🏠 使用住宅或移动代理

| 代理类型 | 可检测性 | 速度 | 适用场景 |
|---------|---------|------|---------|
| 数据中心 | 高 | 非常快 | 低安全性网站 |
| ISP/静态住宅 | 中 | 快 | 基于账户的爬虫 |
| 住宅 | 低 | 中 | 电商、旅游、社交 |
| 移动 | 非常低 | 中 | 严格网站（含 Cloudflare 等） |

### 3. 📋 设置完整的真实请求标头

`requests` 库默认对标头极少，而 Chrome 会发送一整套标头。需模拟：

```
Accept, Accept-Language, Accept-Encoding, Connection,
Referrer, Sec-Fetch-Site, Sec-Fetch-Mode, Sec-Fetch-Dest,
Upgrade-Insecure-Requests 等
```

### 4. 🔀 轮换 User-Agent

构建近期活跃的 UA 池并轮换，保持内部一致性（操作系统、浏览器类型与 Accept-Language 需匹配）。

### 5. 🔐 管理 TLS 指纹识别

**关键且常被忽视。** Python 的 `requests` 底层 OpenSSL 的 TLS 握手会产生明显的非浏览器 JA3 值。

```python
from curl_cffi import requests as curl_requests
response = curl_requests.get("https://example.com", impersonate="chrome121")
```

### 6. 🕵️ 使用 Stealth 插件的无头浏览器

默认无头 Chrome 暴露 `navigator.webdriver=true` 和 `HeadlessChrome` UA，Cloudflare Turnstile 可毫秒级检测。

```python
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    stealth_sync(page)
    page.goto("https://example.com")
```

> ⚠️ stealth 插件会**降低**而非消除检测风险。

### 7. ⏱️ 随机化请求时间和行为

使用高斯分布模拟人类行为，而非固定间隔：

```python
def human_delay(mean=4.0, std=1.5, min_delay=1.0):
    delay = np.random.normal(mean, std)
    time.sleep(max(delay, min_delay))
```

同时模拟滚动、鼠标移动等真实会话行为。

### 8. 🧩 自动处理 CAPTCHA

三种方式：
- **第三方破解服务**：2captcha、Anti-Captcha
- **reCAPTCHA v3 分数管理**：通过良好会话卫生保持高分
- **托管解决方案**：如 Web Unlocker 内置破解

### 9. 🪤 避免蜜罐陷阱

```python
# 检查 CSS 可见性
element = page.query_selector('a.some-link')
if element and element.is_visible():
    element.click()
```

跳过 `display: none` 或 `visibility: hidden` 的元素，以及常见蜜罐类名（`hidden`, `invisible`, `honeypot` 等）。

### 10. 📈 指数退避处理速率限制

```python
import time, random

def fetch_with_retry(url, max_retries=5):
    for attempt in range(max_retries):
        response = requests.get(url)
        if response.status_code in [429, 403, 503]:
            wait = 2 ** attempt + random.uniform(0, 1)
            # 优先使用 Retry-After 响应头
            retry_after = response.headers.get('Retry-After')
            if retry_after:
                wait = int(retry_after)
            time.sleep(wait)
        else:
            return response
    return None
```

### 11. 🌍 匹配地理上下文

通过与实际请求来源匹配的地理位置的代理进行路由，避免地理位置与 `Accept-Language` 的不一致。

### 12. ⚡ 利用底层 API

检查浏览器 Network 标签页，寻找网站内部使用的 JSON API 端点——比解析 HTML 更简单，反机器人审查通常更低。

---

## 四、主流反机器人系统及应对策略

| 系统 | 主要检测手段 | 应对策略 |
|------|-------------|---------|
| **Cloudflare** | Turnstile、IP 信誉、JA4 指纹、行为分析 | 完整浏览器 + 住宅代理 + 正确 TLS |
| **Akamai** | 客户端传感器数据（canvas/字体/WebGL）、`abck` cookie 验证 | 多层同时修复 |
| **DataDome** | 实时 ML 评分、IP ASN、请求节奏、JS 信号 | 移动住宅 IP + 持久会话 |
| **PerimeterX/HUMAN** | 全会话行为分析（鼠标移动、按键、滚动深度） | 模拟完整人类行为 |

---

## 五、封锁机制与应对速查表

| 封锁机制 | 推荐技术 |
|---------|---------|
| IP 封禁 / 速率限制 | 代理轮换 |
| 数据中心 IP 检测 | 住宅 / 移动代理 |
| TLS 指纹识别 | `curl_cffi` 浏览器模拟 |
| 浏览器指纹识别 | 无头浏览器 + stealth 插件 |
| CAPTCHA 挑战 | 自动化破解服务 |
| 行为分析 | 随机化时间 + 模拟人类操作 |
| 蜜罐陷阱 | 跳过隐藏链接 |
| JavaScript 挑战 | 完整浏览器渲染 |
| 地理封锁 | 地理定向代理 |
| 速率限制 | 指数退避 |

---

## 六、工具与库推荐

| 工具 | 用途 |
|------|------|
| **Playwright** | 浏览器自动化（无头浏览器） |
| **playwright-stealth** | 反检测插件（覆盖指纹伪装） |
| **curl_cffi** | TLS 指纹模拟（Chrome/Firefox） |
| **undetected-chromedriver** | Selenium 反检测方案 |
| **2captcha / Anti-Captcha** | CAPTCHA 破解服务 |
| **BeautifulSoup** | HTML 解析（配合蜜罐检测） |

---

## 七、商业解决方案

| 产品 | 功能 |
|------|------|
| **Web Unlocker** | AI 驱动的代理网关，自动处理 IP 轮换、TLS 匹配、CAPTCHA、浏览器渲染 |
| **Scraping Browser** | 预加固 Chromium，通过 CDP 集成 Playwright/Puppeteer，无需插件 |
| **Scraper API** | 120+ 现成爬虫（Amazon、LinkedIn、Instagram 等），输出 JSON/CSV |
| **住宅代理** | 4亿+ IP，覆盖 195+ 国家/地区 |
| **移动代理** | 700万+ 移动 IP |

---

## 八、关键结论

> **没有一种单一技术可以击败所有反机器人系统。**

现代检测多层覆盖 IP 信誉、TLS 指纹、浏览器指纹和行为分析——绕过需要**同时匹配所有层**。

| 场景 | 推荐方案 |
|------|---------|
| **开发 / 低流量** | 住宅代理 + 真实标头 + `curl_cffi` + Playwright with stealth |
| **生产规模** | 托管解决方案（多层整合为单次 API 调用） |

---

## 九、法律提示

- 抓取**公开数据**在多数司法管辖区通常合法（hiQ v. LinkedIn 裁决确认公众数据抓取不违反 CFAA）
- 应检查 `robots.txt` 和网站条款
- 个人数据受 GDPR、CCPA 等限制
- LinkedIn 等平台有明确的 ToS 禁止自动化爬取，违规可能面临账号封禁和法律诉讼

---

> 📝 整理日期：2026-07-08
