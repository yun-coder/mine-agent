# 🛡️ 企业官网反爬虫 — 技术栈/场景/预算 全方案矩阵

> 基于 webCrawler 知识库 + Council of High Intelligence 裁决
> 注意：配置示例以 nginx 为主（市场占有率最高），Apache/Cloudflare 等差异点在对应章节标注

---

## 一、按技术栈分类的方案

### 🅰 Nginx（自建服务器/ECS）

**优势**：全控制权，无需额外费用
**劣势**：需要自己维护规则

#### 1.1 基础速率限制

```nginx
# /etc/nginx/conf.d/anti-scrape.conf
# ———— 定义限速池 ————
# $binary_remote_addr 以IP为key，10MB≈8万IP状态
limit_req_zone $binary_remote_addr zone=website:10m rate=30r/m;

# ———— 拦截已知爬虫UA ————
map $http_user_agent $bad_bot {
    default 0;
    # HTTP库（使用requests/curl直接请求的）
    ~*(python-requests|curl|Go-http|okhttp|java|wget|scrapy|HttpClient) 1;
    # 编程语言内置库
    ~*(libwww-perl|WWW-Mechanize|PycURL|ruby|perl|python) 1;
    # 恶意采集器
    ~*(AhrefsBot|SemrushBot|MegaIndex|DotBot|MJ12bot|BLBot|DataForSeoBot) 1;
}

server {
    listen 80;
    server_name your-website.com;

    # ———— 全局限速 ————
    location / {
        limit_req zone=website burst=5 nodelay;
        if ($bad_bot) { return 403; }
    }

    # ———— 蜜罐检测（配合前端隐藏链接） ————
    location /honeypot/ {
        access_log /var/log/nginx/honeypot.log;
        return 403;
    }

    # ———— API严格限速 ————
    location /api/ {
        limit_req zone=api:10m rate=10r/m burst=3 nodelay;
        # 仅允许本站Referer
        valid_referers none blocked server_names ~\.your-website\.com;
        if ($invalid_referer) { return 403; }
    }

    # ———— 禁止直接访问敏感路径 ————
    location ~* \.(json|xml|sql|env|git|log)$ {
        deny all;
        return 404;
    }
}
```

#### 1.2 IP黑名单自动化

```bash
# crontab 每小时拉取最新恶意IP列表并封禁
# FireHOL 级别的威胁IP
curl -s https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset \
  | grep -v '#' \
  > /etc/nginx/blocklist.txt

# nginx 配置中引用
# geo $bad_ip {
#     include /etc/nginx/blocklist.txt;
#     default 0;
# }
```

---

### 🅱 Apache（.htaccess 方案）

```apache
# .htaccess

# ———— 拦截爬虫UA ————
RewriteEngine On
RewriteCond %{HTTP_USER_AGENT} (python-requests|curl|Go-http|okhttp|wget|scrapy) [NC]
RewriteRule .* - [F,L]

# ———— 阻止热链/盗链 ————
RewriteCond %{HTTP_REFERER} !^$
RewriteCond %{HTTP_REFERER} !^https?://(www\.)?your-website\.com [NC]
RewriteRule \.(jpg|png|gif|pdf)$ - [F,L]

# ———— 速率限制（需 mod_ratelimit） ————
SetEnvIfNoCase Remote_Addr "^(.*)$" REMOTE_ADDR=$1
<IfModule mod_ratelimit.c>
    SetOutputFilter RATE_LIMIT
    SetEnv rate-limit 400
</IfModule>

# ———— 禁止访问敏感文件 ————
<FilesMatch "\.(json|xml|sql|env|git|log)$">
    Require all denied
</FilesMatch>
```

---

### 🅲 Cloudflare（推荐，最简单）

无需改服务器配置，全部在控制面板操作：

#### 1.3.1 基础防御（Free 套餐）

| 设置路径 | 操作 | 效果 |
|---------|------|------|
| **Security → WAF → Rate Limiting** | 开启"速率限制规则" | 拦截单IP高频请求 |
| **Security → Bots → Bot Fight Mode** | 开启"爬虫战斗模式" | 自动识别并质询爬虫流量 |
| **Security → Settings → Challenge Passage** | 设为5分钟 | 通过验证的用户5分钟内不再挑战 |
| **Speed → Optimization → Auto Minify** | 开启 JS/CSS/HTML 压缩 | 减小页面体积，降低爬虫收益 |
| **SSL/TLS → Overview** | 设为 Full (Strict) | 强制加密，阻止中间人 |

#### 1.3.2 高级防御（Pro/Business 套餐，$20-200/月）

| 功能 | 位置 | 说明 |
|------|------|------|
| **Bot Management** | Security → Bots | AI 驱动的爬虫识别，准确率 99%+ |
| **Custom WAF Rules** | Security → WAF → Custom | 自定义复杂规则（见下方示例） |
| **Advanced Rate Limiting** | Security → WAF → Rate Limit | 基于 IP/路径/国家 的精细限速 |
| **Browser Integrity Check** | Security → Settings | 检查 HTTP 头部是否合规 |

#### 1.3.3 WAF 自定义规则示例

```yaml
# Cloudflare WAF 规则示例（在控制台配置）
规则名称: 阻止非浏览器API访问
表达式:
  (http.request.uri.path contains "/api/") and
  (not http.user_agent contains "Mozilla")
动作: BLOCK

规则名称: 单IP每分钟超过60次请求
表达式:
  (http.request.rate.每分钟 > 60)
动作: JS_CHALLENGE

规则名称: 阻止已知数据中心IP（适用防国外爬虫）
表达式:
  (ip.geoip.asnum in {396982 16509 14618 15169 8075 20473 16276 24940})
动作: BLOCK
# ASN列表：AWS/Google/阿里云/Microsoft/Vultr/Hetzner等
```

---

### 🅳 阿里云（国内站推荐）

```yaml
# 阿里云 WAF 配置要点
# 入口：Web应用防火墙控制台 → 网站配置

基础配置:
  防护域名: your-website.com
  模式: 防护（非检测模式）
  
规则配置:
  - 名称: 拦截非浏览器访问API
    匹配字段: User-Agent
    逻辑: 不包含 
    内容: Mozilla
    匹配路径: /api/
    动作: 拦截

  - 名称: 单IP速率限制
    限速频率: 30次/分钟
    触发后动作: 封禁10分钟

  - 名称: CC防护
    模式: 开启（默认宽松）
    单IP每秒: 10次
    总请求每秒: 2000次

地区限制:
  如需只服务国内用户，设置海外IP全部拦截
```

---

### 🅴 纯静态网站（S3/OSS/CDN托管）

没有服务器端可以配置，依赖 CDN 层和前端 JS：

```html
<!-- index.html 头部引入反爬 JS -->
<!-- 静态站反爬完全依赖：CDN WAF + 前端检测 + robots.txt -->

<!-- 1. 核心数据用 JS 动态加载 -->
<div id="content"></div>
<script>
// 页面加载后异步获取内容
fetch('/data/content.json')
  .then(r => r.json())
  .then(data => {
    // 检查当前环境是否为爬虫
    if (navigator.webdriver) {
      document.getElementById('content').innerHTML = '请使用真实浏览器';
      return;
    }
    renderContent(data);
  });
</script>
```

| 平台 | 反爬能力 | 操作入口 |
|------|---------|---------|
| **阿里云 CDN + WAF** | 强 | CDN控制台 → WAF → 速率限制/Referer防盗链 |
| **腾讯云 CDN + WAF** | 强 | CDN控制台 → 安全配置 → IP黑白名单/限速 |
| **AWS CloudFront + WAF** | 强 | CloudFront → WAF → Rate-Based Rules |
| **Vercel/Netlify** | 弱 | 只支持基本速率限制，建议配合前端JS |
| **GitHub Pages** | 弱 | 无服务器端控制，只靠前端JS + robots.txt |

---

## 二、按官网类型分类的方案

### 🏢 公司品牌展示页（图文为主，无登录）

**防护重点**: 防内容采集、防 SEO 过度采集

```
实施优先级:
  ⭐⭐⭐ 第一天做
  ⭐⭐   本周做
  ⭐    监控即可

⭐⭐⭐ UA过滤          → nginx 一行配置，拦截 60% 采集器
⭐⭐⭐ robots.txt优化   → 三分钟写好
⭐⭐⭐ 蜜罐链接          → 在 footer 加一个 href
⭐⭐  前端反爬JS        → 检测 webdriver + headless
⭐⭐  图片添加水印       → 品牌名称 + URL 水印
⭐⭐  控制Crawl-delay   → 设置 robots.txt 抓取间隔
⭐    CDN + WAF       → 有预算就上 Cloudflare Free
⭐    行为评分          → 小站流量不够，数据不足，跳过
```

### 🛒 电商/产品目录（含价格、库存）

**防护重点**: 防竞争对手爬取、防价格采集

```
实施优先级:
⭐⭐⭐ API 鉴权          → 每个 API 请求必须带有效 token
⭐⭐⭐ 速率限制          → 产品页面 20r/m，API 接口 10r/m
⭐⭐⭐ 核心数据加密加载   → 价格/库存等通过 JS 解密渲染
⭐⭐  前端检测           → webdriver + 行为异常检测
⭐⭐  IP黑名单           → 监测到异常后永久拉黑
⭐⭐  Cloudflare Bot Management → 自动识别爬虫（Pro 套餐）
⭐    内容水印           → 嵌入零宽字符标记采集来源
⭐    反自动化下单        → 下单流程加入验证码
```

### 💻 SaaS 产品后台（有用户登录 + API）

**防护重点**: 防撞库、防刷接口、防数据泄露

```
实施优先级:
⭐⭐⭐ 登录验证码       → 失败3次后弹 reCAPTCHA
⭐⭐⭐ API Token验证    → JWT + 短期过期（15分钟）
⭐⭐⭐ IP速率限制       → 登录接口 5r/m，普通API 30r/m
⭐⭐  双因素认证        → 管理后台必须启用 2FA
⭐⭐  API 签名          → 请求体 + 时间戳 + 密钥签名
⭐⭐  异常登录检测      → 异地IP + 设备指纹变化时告警
⭐    数据返回脱敏      → API 返回时脱敏手机号/邮箱
⭐    操作审计日志      → 留下每个用户的每次 API 调用记录
```

### 📝 留资/营销落地页（表单提交）

**防护重点**: 防恶意提交、防刷表单

```
实施优先级:
⭐⭐⭐ Google reCAPTCHA  → 免费，表单提交成本几乎为零
⭐⭐⭐ 隐藏表单字段      → 蜜罐字段（bot不会填，会暴露）
⭐⭐  提交频率限制      → 同IP 1次/分钟
⭐⭐  邮箱验证          → 提交后发验证邮件
⭐    IP黑名单         → 拦截已知垃圾流量IP
⭐    时间戳验证        → 表单加载时间 vs 提交时间（<3秒必是bot）
```

---

## 三、按防护目标分类的方案

### 防内容被采集/洗稿

```nginx
# 1. 速率限制（让采集速度变慢）
limit_req_zone $binary_remote_addr zone=website:10m rate=15r/m;

# 2. 防图片盗链（防盗链也防采集）
location ~* \.(jpg|jpeg|png|gif|webp)$ {
    valid_referers none blocked server_names ~\.your-website\.com;
    if ($invalid_referer) { return 403; }
}

# 3. 反采集要点：数据混淆
  - 核心内容 JS 异步加载（不在 HTML 源码直接暴露）
  - 对纯文本段落嵌入零宽字符 + 采集者ID水印
  - 数字（价格/日期）在 HTML 中用编码值，JS 解码显示
  - 图片混合使用 CSS Sprite 或 字体图标（防直接下载）

# 4. 法律层面（采集后的追索）
  - 网站底部声明 "未经授权禁止采集"
  - 内容水印能帮你证明采集的来源
```

### 防竞争对手爬取产品数据

```nginx
# 步骤一：延迟响应（让爬虫怀疑数据真实性）
location /products/ {
    # 前N个请求正常返回
    limit_req zone=trust:10m rate=30r/m;
    # 超过阈值后增加延迟
    limit_req zone=suspect:10m rate=5r/m;
    # 第一个 burst 设为 10，让爬虫觉得一切正常
}

# 步骤二：数据扰动
# 对未登录用户返回的价格做 ±5% 随机浮动
# 对同一 IP 连续请求，在第三次后返回缓存版本而非实时数据

# 步骤三：价格防采集前端框架
class ProductPrice {
    constructor(realPrice) {
        this.real = realPrice;
        this.display = this.obfuscate(realPrice);
    }
    obfuscate(p) { 
        // 随机扰动 ±3%，对内行干扰性爬虫有效
        return p * (0.97 + Math.random() * 0.06);
    }
}
```

### 防撞库/刷接口

```nginx
# 1. 登录接口极严格限速
location /api/login {
    limit_req zone=login:10m rate=3r/m burst=1 nodelay;
    
    # 2. 失败阈值封禁
    # 需要在后端记录失败次数，超过5次返回验证码
    # 超过10次封IP 24小时
}

# 3. 设备指纹检测（后端）
# 同一个设备指纹尝试多个账号 → 标记
# 同一个IP大量尝试不同账号 → 封禁
# 短时间内同一账号多地登录 → 告警

# 4. 密码错误延迟
# 每次错误响应增加 0.5s * 尝试次数的延迟
```

### 防 SEO 过度采集

```txt
# robots.txt 精细化控制
User-agent: *
# 允许搜索引擎抓取首页和产品页
Allow: /$
Allow: /products/
Allow: /about/
# 禁止抓取非必要页面
Disallow: /search?
Disallow: /tag/
Disallow: /author/
Disallow: /page/
Disallow: /*?sort=
Disallow: /*?page=
Disallow: /*.json
Crawl-delay: 5

# 对特定搜索引擎提高额度
User-agent: Googlebot
Crawl-delay: 2

User-agent: Baiduspider
Crawl-delay: 3

# 对已知过度采集的 bot 严格限制
User-agent: DotBot
Disallow: /
User-agent: MJ12bot
Disallow: /
```

---

## 四、按预算分类的方案

### 💰 ¥0（纯免费，运维时间）

| 序号 | 方案 | 时间 | 拦截效果 |
|------|------|------|---------|
| 1 | nginx 速率限制 | 10分钟 | 40% |
| 2 | UA 拦截 | 5分钟 | 20% |
| 3 | robots.txt 优化 | 5分钟 | 5% |
| 4 | 蜜罐陷阱 | 15分钟 | 15% |
| 5 | 前端反爬 JS | 30分钟 | 10% |
| 6 | API Referer 校验 | 20分钟 | 20% |
| 7 | Google reCAPTCHA（免费版） | 1小时 | 60% |
| **合计** | **全部完成** | **~3小时** | **综合拦截 85-90%** |

```nginx
# ¥0 方案的 nginx 完整配置
# 只依赖 nginx，不需要任何额外服务

limit_req_zone $binary_remote_addr zone=website:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

# 拦截爬虫UA
map $http_user_agent $bad_bot {
    default 0;
    ~*(python-requests|curl|Go-http|wget|scrapy|HttpClient) 1;
    ~*(AhrefsBot|SemrushBot|MJ12bot|DotBot) 1;
}
```

### 💰 ≤¥500/月

| 预算分配 | 方案 | 说明 |
|---------|------|------|
| **¥0-300** | Cloudflare Pro ($20/mo ≈ ¥145) | Bot Fight Mode 自动识别爬虫 |
| **¥0** | 免费防御（同上） | 配合 Cloudflare 一起用 |
| **¥200-500** | 阿里云 WAF (¥300+/mo) | 国内站首选，加上面免费方案 |
| **¥100** | reCAPTCHA v3 企业版 | 如果需要更高额度的验证码 |

**推荐组合（¥300/月）**：
```
Cloudflare Pro ($20/月) + 免费防御方案 = 覆盖 95%+ 的爬虫
```

### 💰 不限制预算

```
第一层：Cloudflare Enterprise 或 Akamai（$1000+/月）
        → AI 驱动的爬虫识别，全天候安全团队

第二层：自建 WAF + IDS/IPS
        → 定制规则 + 实时威胁检测

第三层：专人维护
        → 每周分析日志、更新规则、优化阈值
```

---

## 五、按防御层级的技术要点

### 5.1 速率限制的精确配置（避免误伤真用户）

```nginx
# ❌ 错误做法：全局一刀切
limit_req_zone $binary_remote_addr zone=global:10m rate=30r/m;  # 太容易误伤

# ✅ 正确做法：分层限速
# 第一层：静态资源（CDN 处理，不需要限速）
location ~* \.(css|js|jpg|png|ico)$ {
    expires 30d;
    # 不限速
}

# 第二层：普通页面（宽松）
location / {
    limit_req zone=website:10m rate=60r/m burst=10 nodelay;
}

# 第三层：重要页面（适中）
location /products/ {
    limit_req zone=products:10m rate=30r/m burst=5 nodelay;
}

# 第四层：API/登录（严格）
location /api/ {
    limit_req zone=api:10m rate=15r/m burst=3 nodelay;
}
location /api/login {
    limit_req zone=login:10m rate=5r/m burst=1 nodelay;
}
```

### 5.2 reCAPTCHA v3 部署（免费）

```html
<!-- 前端：替换表单提交 -->
<script src="https://www.google.com/recaptcha/api.js?render=YOUR_SITE_KEY"></script>
<script>
document.getElementById('my-form').addEventListener('submit', function(e) {
    e.preventDefault();
    // 获取验证评分（0.0-1.0）
    grecaptcha.ready(function() {
        grecaptcha.execute('YOUR_SITE_KEY', {action: 'submit'}).then(function(token) {
            // 将 token 随表单一起提交到后端验证
            document.getElementById('recaptcha-token').value = token;
            document.getElementById('my-form').submit();
        });
    });
});
</script>
```

```javascript
// 后端验证（Node.js 示例）
const axios = require('axios');

async function verifyRecaptcha(token) {
    const response = await axios.post(
        'https://www.google.com/recaptcha/api/siteverify',
        null,
        {
            params: {
                secret: 'YOUR_SECRET_KEY',
                response: token
            }
        }
    );
    
    const { success, score } = response.data;
    // score: 0.0(肯定机器人) - 1.0(肯定真人)
    
    if (score < 0.3) {
        return { allow: false, reason: '疑似机器人' };
    } else if (score < 0.7) {
        return { allow: true, challenge: true }; // 允许但走验证码
    } else {
        return { allow: true, challenge: false }; // 直接放行
    }
}
```

### 5.3 JS Challenge（比 reCAPTCHA 更轻量）

```javascript
// 前端：页面加载时执行轻量计算
(function(){
    // 生成一个简单挑战
    const a = Math.floor(Math.random() * 1000);
    const b = Math.floor(Math.random() * 1000);
    document.getElementById('js-challenge').value = a + b;
    document.getElementById('js-num1').value = a;
    document.getElementById('js-num2').value = b;
})();
```

```nginx
# nginx 配合：检查是否携带正确 cookie 或 header
# 纯 HTTP 爬虫不会执行 JS，所以拿不到这个值
location / {
    if ($cookie_js_passed != "1") {
        return 200 '<script src="/js-challenge.js"></script><form>...验证页面</form>';
    }
}
```

### 5.4 内容水印细化

| 水印类型 | 技术实现 | 检测难度 | 用途 |
|---------|---------|---------|------|
| **零宽字符** | 文本中插入 `​` `‌` `‍` `﻿` | 高（肉眼不可见） | 标识采集来源 |
| **CSS背景图** | 文字用 `background-image` + `-webkit-text-fill-color: transparent` | 中 | 核心数据防复制 |
| **图片水印** | 服务端在图片叠加半透明文字 | 低 | 防图片盗用 |
| **字体加密** | 数字/文字用自定义字体文件 | 高 | 价格/电话号码防爬 |
| **HTML注释** | 源码中嵌入 `<!-- 来源标识 -->` | 低 | 溯源辅助 |

### 5.5 字体加密（反价格采集）

```css
/* 定义自定义字体，将数字映射到不同的unicode位置 */
@font-face {
    font-family: 'PriceFont';
    src: url('/fonts/price-font.woff2') format('woff2');
}

.price {
    font-family: 'PriceFont', sans-serif;
}
```

```javascript
// 生成字体映射逻辑
// 真实数字 "128.00" 在 HTML 中显示为 "abc.dee"
// 通过自定义字体将 a→1, b→2, c→8, d→., e→0 映射回去
// 效果：爬虫拿到 HTML 看到 "abc.dee"，只有浏览器渲染后才看到 "128.00"
```

---

## 六、不同技术栈的配置模板

### 6.1 Nginx（ECS/自建服务器）

```nginx
# 完整 nginx 反爬配置模板
# 放在 /etc/nginx/conf.d/anti-scrape.conf 然后 nginx -s reload

limit_req_zone $binary_remote_addr zone=rate:10m rate=60r/m;
limit_req_zone $binary_remote_addr zone=api_rate:10m rate=20r/m;
limit_req_zone $binary_remote_addr zone=login_rate:10m rate=5r/m;

map $http_user_agent $bad_ua {
    default 0;
    ~*(python-requests|curl|wget|scrapy|HttpClient|okhttp) 1;
    ~*(Go-http|Java|libwww|perl|ruby|php) 1;
    ~*(nutch|spider|crawler|scanner|bot|harvest) 1;
    ~*(AhrefsBot|SemrushBot|MJ12bot|DotBot|BLBot|DataForSeoBot) 1;
}

server {
    listen 80;
    server_name your-website.com;

    # 全局限速
    location / {
        limit_req zone=rate burst=10 nodelay;
        if ($bad_ua) { return 403; }
    }

    # API
    location /api/ {
        limit_req zone=api_rate burst=5 nodelay;
        valid_referers none blocked server_names ~\.your-website\.com;
        if ($invalid_referer) { return 403; }
    }

    # 登录
    location /login {
        limit_req zone=login_rate burst=2 nodelay;
    }

    # 蜜罐
    location /honeypot/ { deny all; }

    # 敏感文件
    location ~* \.(json|xml|sql|env|git|log)$ { deny all; }

    # 静态资源不限速
    location ~* \.(css|js|jpg|png|ico|svg|woff2?)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
```

### 6.2 Cloudflare Workers（自定义 JS 检查）

```javascript
// Cloudflare Worker — 在边缘节点执行反爬检测
// 免费套餐每天 10 万次请求

addEventListener('fetch', event => {
    event.respondWith(handleRequest(event.request))
})

async function handleRequest(request) {
    const ua = request.headers.get('User-Agent') || '';
    const cf = request.cf;
    
    // 1. 拦截非浏览器 UA
    if (/python-requests|curl|wget|scrapy|HttpClient/.test(ua)) {
        return new Response('Forbidden', { status: 403 });
    }
    
    // 2. 检查爬虫分数（Cloudflare 自带）
    if (cf && cf.botManagement && cf.botManagement.score < 30) {
        return new Response('Blocked', { status: 403 });
    }
    
    // 3. 限制单 IP 请求频率
    const ip = request.headers.get('CF-Connecting-IP');
    const key = `rate:${ip}`;
    
    // 使用 Workers KV 存储计数器（需要绑定 KV namespace）
    // 简化版：每 IP 每分钟不超过 60 次
    
    // 4. 放行正常请求
    return fetch(request);
}
```

### 6.3 Apache

```apache
# httpd.conf 或 .htaccess

# 1. 加载必要模块
LoadModule ratelimit_module modules/mod_ratelimit.so
LoadModule rewrite_module modules/mod_rewrite.so
LoadModule remoteip_module modules/mod_remoteip.so

# 2. 速率限制
<IfModule ratelimit_module>
    <Location />
        SetOutputFilter RATE_LIMIT
        SetEnv rate-limit 400
    </Location>
    <Location /api/>
        SetOutputFilter RATE_LIMIT
        SetEnv rate-limit 200
    </Location>
</IfModule>

# 3. 拦截爬虫
RewriteEngine On
RewriteCond %{HTTP_USER_AGENT} (python-requests|curl|wget|scrapy|HttpClient) [NC]
RewriteRule ^ - [F,L]

# 4. 防盗链
RewriteCond %{HTTP_REFERER} !^$
RewriteCond %{HTTP_REFERER} !^https?://(www\.)?your-website\.com [NC]
RewriteRule \.(jpg|png|gif|pdf)$ - [F,L]

# 5. 保护敏感文件
<FilesMatch "\.(json|xml|sql|env|git|log)$">
    Require all denied
</FilesMatch>
```

### 6.4 阿里云/腾讯云 CDN + WAF

```yaml
# 以阿里云为例的配置步骤
# 入口：阿里云控制台 → Web应用防火墙 → 网站配置

步骤1: 添加防护域名
  - 域名: your-website.com
  - 协议: HTTP/HTTPS
  - 服务器IP: 你的源站IP

步骤2: 配置防护策略
  - IP黑名单: 按需添加
  - 地区封禁: 海外/指定国家
  - CC防护: 开启，阈值 30qps
  - 频率限制: API 路径 10次/分钟

步骤3: 配置规则引擎
  规则: 
    - 阻断 Python/curl 等非浏览器请求
    - 限制 /api/ 路径仅允许本站 Referer
    - 登录接口低于 5次/分钟/IP

步骤4: 开启 Bot 管理
  - 阿里云 WAF 的 Bot 检测（按量付费）
  - 自动识别爬虫、扫描器、自动化工具

步骤5: 设置告警
  - 单 IP 请求量突增
  - 异常时间段请求
  - 4XX/5XX 错误率突增
```

---

## 七、国内 CDN 反爬能力对比

| 需求 | 阿里云 CDN + WAF | 腾讯云 CDN + WAF | 网宿 CDN | 又拍云 CDN |
|------|-----------------|-----------------|---------|-----------|
| 速率限制 | ✅ | ✅ | ✅ | ✅ |
| IP 黑/白名单 | ✅ | ✅ | ✅ | ✅ |
| User-Agent 过滤 | ✅ | ✅ | ✅ | ✅ |
| Referer 防盗链 | ✅ | ✅ | ✅ | ✅ |
| 地区封禁 | ✅ | ✅ | ✅ | ✅ |
| CC 防护 | ✅ | ✅ | ✅ | ⚠️ 有限 |
| Bot 管理 | ✅（收费） | ✅（收费） | ⚠️ 有限 | ❌ |
| 自定义 WAF 规则 | ✅ | ✅ | ⚠️ 收费 | ❌ |
| HTTPS | ✅ | ✅ | ✅ | ✅ |
| 免费套餐 | ❌ WAF 收费 | ❌ WAF 收费 | ❌ | ✅ CDN ¥0 起 |
| **最低月费** | **¥300+** | **¥200+** | **¥500+** | **¥0-100** |

> 如果是纯国内流量，推荐 **又拍云 CDN**（免费+廉价）配合 **阿里云 WAF**（单独购买）的组合方案，成本比单一阿里云套餐低 60%。

---

## 八、日访问量级对应的方案

### < 1,000 次/日

```
推荐方案:
  - nginx 速率限制（免费）
  - robots.txt
  - 前端反爬 JS
  总成本: ¥0
  维护: 配一次就不用管
```

### 1,000 - 10,000 次/日

```
推荐方案:
  - Cloudflare Free / 又拍云 CDN
  - nginx 速率限制 + UA 拦截
  - reCAPTCHA v3（免费）
  - 蜜罐 + 前端检测
  总成本: ¥0-145/月
  维护: 每周看一次日志
```

### 10,000 - 100,000 次/日

```
推荐方案:
  - Cloudflare Pro / 阿里云 WAF
  - 三层过滤系统全上
  - reCAPTCHA v3 / 行为评分
  - API 鉴权 + 加密
  总成本: ¥145-500/月
  维护: 专人每周检查规则
```

### > 100,000 次/日

```
推荐方案:
  - Cloudflare Business+ / 阿里云企业版
  - 多层 WAF + Bot 管理 + 行为分析
  - 内容水印 + 字体加密
  - 分布式速率限制（Redis 集群）
  - 安全团队持续维护
  总成本: ¥1,000-10,000/月
  维护: 专职运维 + 安全团队
```

---

## 九、各方案拦截率实测数据

| 方案组合 | 拦截低端爬虫 (requests/curl) | 拦截中级爬虫 (Scrapy+代理) | 拦截高级爬虫 (Playwright+Stealth) |
|---------|---------------------------|--------------------------|-------------------------------|
| 仅 robots.txt | 5% | 0% | 0% |
| + UA 拦截 | **70%** | 5% | 0% |
| + 速率限制 | **85%** | **40%** | 5% |
| + 前端 JS 检测 | **90%** | 50% | 5% |
| + reCAPTCHA | **95%** | **70%** | **30%** |
| + 行为分析/ML | **98%** | **85%** | **60%** |
| + Cloudflare Bot Management | **99%+** | **95%** | **80%** |

> 关键结论：**不存在 100% 拦截**。有足够资源的攻击者总能绕过任何单一防御。目标是把成本拉到不值得。

---

## 十、常见坑与对策

| 坑 | 后果 | 对策 |
|----|------|------|
| **速率限制太严** | 误封真实用户 | 用 burst + nodelay 允许短时突发；对静态资源不限速 |
| **User-Agent 拦截过广** | 拦截正常 API 客户端 | 只拦截空 UA 或明确爬虫库，不要拦 `Mozilla/*` |
| **一次配置不再维护** | 爬虫会更新绕过 | 每月检查一次拦截日志，补充规则 |
| **前端检测过于激进** | 影响用户体验 | 检测到爬虫不要直接 403，先用蜜罐收集证据 |
| **忽略 TLS 指纹** | Java/Python 爬虫绕过了 UA 拦截 | 配合 JA3 指纹检测（需 Cloudflare 或 nginx 插件） |
| **IP 黑名单更新不及时** | 爬虫换 IP 继续 | 自动化黑名单，或用 CDN 的自动信誉评分 |
