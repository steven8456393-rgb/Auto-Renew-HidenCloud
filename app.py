#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os,re,sys,time,random,requests
try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

# --- 环境变量 ---
COOKIE_VALUE = os.environ.get('COOKIE_VALUE') or ""    # remember_web cookie 值，必填
EMAIL        = os.environ.get('EMAIL') or ""           # 登录邮箱,可选，作为备用,TG通知需要填写
PASSWORD     = os.environ.get('PASSWORD') or ""        # 登录密码,可选，作为备用
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""    # Telegram Bot Token,可选
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID') or ""      # Telegram Chat ID,可选

BASE_URL = "https://dash.hidencloud.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

# --- 代理配置（由工作流 shell 脚本写入 $GITHUB_ENV）---
IS_PROXY      = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER  = os.environ.get('PROXY_SERVER') or "socks5://127.0.0.1:1080"
REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

# 日志
def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""

def get_current_ip(proxy_server=None):
    """获取当前出口IP"""
    proxies = {"http": proxy_server, "https": proxy_server} if (proxy_server and IS_PROXY) else None
    try:
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        # log(f"请求出口IP完成, status={resp.status_code}")
        if resp.status_code == 200:
            return resp.text.strip()
        return "获取失败"
    except Exception as e:
        log(f"❌ 获取出口IP失败: {e}")
        return "获取失败"

def send_telegram_notification(status, old_due, new_due):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False
    
    # 获取运行时间
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****' 

    text = (
        f"🎉 HidenCloud 续期通知\n\n"
        f"{status}\n"
        f"👤 账号: {masked_email}\n"
        f"📅 续期前到期：{old_due}\n"
        f"📅 续期后到期：{new_due}\n"
        f"🕒 续期时间：{now}"
    )
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10, proxies=REQUESTS_PROXIES)
        if resp.status_code == 200:
            log("✅ Telegram 通知发送成功")
            return True
        else:
            log(f"❌ Telegram 通知失败: {resp.text}")
            return False
    except Exception as e:
        log(f"❌ Telegram 通知异常: {e}")
        return False

def handle_cloudflare(page):
    iframe_selector = 'iframe[src*="challenges.cloudflare.com"]'
    if page.locator(iframe_selector).count() == 0:
        return True
    log("⚠️ 检测到 Cloudflare 验证...")
    start_time = time.time()
    while time.time() - start_time < 60:
        if page.locator(iframe_selector).count() == 0:
            log("✅ Cloudflare 验证通过！")
            return True
        try:
            frame = page.frame_locator(iframe_selector)
            checkbox = frame.locator('input[type="checkbox"]')
            if checkbox.is_visible():
                log("🖱️ 点击验证复选框...")
                time.sleep(random.uniform(0.5, 1.5))
                checkbox.click()
                log("⏳ 已点击，等待验证结果...")
                time.sleep(5)
            else:
                time.sleep(1)
        except Exception:
            pass
    log("❌ 验证超时。")
    return False

def dismiss_ncmp_banner(page):
    """关掉 ncmp Cookie 横幅（.ncmp__banner-inner）。

    2026-11-12 run #41 实测：该横幅拦截指针事件，导致 Create Invoice
    6 次点击均 20s 超时。先尝试点横幅内的接受/关闭按钮；点不到就直接
    把横幅元素从 DOM 移除（横幅只是合规提示，移除不影响续期表单）。"""
    try:
        banner = page.locator('.ncmp__banner-inner')
        if banner.count() == 0:
            return False
        log("🧹 检测到 ncmp Cookie 横幅，尝试关闭...")
        # 优先点横幅内的按钮（接受/同意/关闭等常见文案）
        closed = False
        for sel in [
            'button:has-text("Accept")', 'button:has-text("Accept All")',
            'button:has-text("I agree")', 'button:has-text("Agree")',
            'button:has-text("OK")', 'button:has-text("Close")',
            '[aria-label="Close"]', '[aria-label="close"]',
        ]:
            try:
                btn = banner.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=3000)
                    log(f"🧹 已点击横幅按钮: {sel}")
                    closed = True
                    break
            except Exception:
                continue
        if not closed:
            # 直接移除横幅元素，避免它继续拦截点击
            removed = page.evaluate("""() => {
                let n = 0;
                document.querySelectorAll('.ncmp__banner-inner, .ncmp__banner, [class*="ncmp__banner"]').forEach(el => { el.remove(); n++; });
                return n;
            }""")
            log(f"🧹 横幅按钮点不到，已从 DOM 移除 {removed} 个横幅元素")
        time.sleep(1)
        return True
    except Exception as e:
        log(f"⚠️ 关闭 ncmp 横幅失败（继续尝试点击）: {e}")
        return False

def login(page):
    # 1. Cookie 登录尝试
    if COOKIE_VALUE:
        # 处理从 DevTools 复制时可能带有的 URL 编码（如末尾 %3D）
        import urllib.parse
        _cookie_val = urllib.parse.unquote(COOKIE_VALUE.strip())
        log("📇 尝试 Cookie 登录...")
        try:
            page.context.add_cookies([{
                'name': 'remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d',
                'value': _cookie_val,
                'domain': 'dash.hidencloud.com',
                'path': '/',
                'expires': int(time.time()) + 3600 * 24 * 365,
                'httpOnly': True,
                'secure': True,
                'sameSite': 'Lax'
            }])
            page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
            handle_cloudflare(page)
            page_title = page.title()
            log(f"📝 当前Title: {page_title}")
            if "auth/login" not in page.url:
                log(f"✅ Cookie 登录成功！当前已到达dashboard页面")
                return True
            log("❌ Cookie 失效，请更换")
        except:
            pass

    # 2. 账号密码登录
    if not EMAIL or not PASSWORD:
        return False
    log("💣 尝试账号密码登录...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        page.fill('input[name="email"]', EMAIL)
        page.fill('input[name="password"]', PASSWORD)
        time.sleep(0.5)
        handle_cloudflare(page)
        page.click('button[type="submit"]')
        time.sleep(3)
        handle_cloudflare(page)
        page.wait_for_url(f"{BASE_URL}/*", timeout=30000)
        page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        page_title = page.title()
        log(f"📝 当前Title: {page_title}")
        if "auth/login" in page.url:
            log("❌ 登录失败。")
            return False
        log(f"✅ 账号密码登录成功！当前已到达dashboard页面")
        return True
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        page.screenshot(path="login_fail.png")
        return False

def get_server_id(page):
    try:
        handle_cloudflare(page)
        time.sleep(3)
        html = page.content()
        log(f"📝 页面长度: {len(html)}, URL: {page.url}")

        # 方案1: 从 href 链接中提取 /service/数字/manage
        matches = re.findall(r'/service/(\d+)/manage', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从链接中获取到 Server ID: {server_id}")
            return server_id

        # 方案2: 从 span 标签中提取 #数字 (如 "Free Server #218079")
        matches = re.findall(r'#(\d{4,})', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从文本 #号中获取到 Server ID: {server_id}")
            return server_id

        log("❌ 所有 URL 均未找到 Server ID")
        return None
    except Exception as e:
        log(f"❌ 获取 Server ID 失败: {e}")
        page.screenshot(path="server_id_error.png")
        return None

def get_due_date(page):
    try:
        if SERVICE_URL not in page.url:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        body_text = page.locator("body").inner_text()
        patterns = [
            r"Due date\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date\s*\n\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date.*?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
            if match:
                due_date = match.group(1).strip()
                log(f"📅 获取到Due Date: {due_date}")
                return due_date
    except Exception as e:
        log(f"❌ 获取Due Date失败: {e}")
    return "未知"

def dump_modal_text(page):
    """把页面上疑似弹窗/对话框的文本打到日志，失败时不再盲猜选择器"""
    try:
        found = False
        for sel in ['div[role="dialog"]', '[class*="modal"]', '[class*="Modal"]', '[class*="dialog"]']:
            loc = page.locator(sel)
            n = loc.count()
            for j in range(min(n, 3)):
                try:
                    t = (loc.nth(j).inner_text() or "").strip()
                except Exception:
                    continue
                if t:
                    found = True
                    log(f"🔍 弹窗候选[{sel}#{j}]: {t[:400]!r}")
        if not found:
            log("🔍 页面上没有找到 role=dialog / modal 类弹窗元素")
    except Exception as e:
        log(f"🔍 弹窗文本抓取失败: {e}")


def renew_service(page):

    try:
        log("➡ 进入续期流程...")
        if page.url != SERVICE_URL:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)

        # 记录服务页状态：过期/暂停服务器的弹窗流程可能与常规不同
        try:
            svc_text = page.locator("body").inner_text()
            for kw in ["expired", "suspend", "terminat", "overdue"]:
                if kw in svc_text.lower():
                    log(f"⚠️ 服务页面出现状态词 '{kw}'，本次可能是过期/暂停服务器的续期流程，弹窗可能与常规不同")
                    break
        except Exception:
            pass

        log("🖱️ 准备点击 'Renew' 按钮...")
        renew_btn = page.locator('button:has-text("Renew")')
        create_btn = page.locator('button:has-text("Create Invoice")')

        modal_opened = False

        # 先诊断 Renew 按钮和 modal 元素状态
        try:
            btn_html = renew_btn.evaluate("el => el.outerHTML.slice(0, 500)")
            is_disabled = renew_btn.evaluate("el => el.disabled")
            is_visible = renew_btn.is_visible()
            is_enabled = renew_btn.is_enabled()
            log(f"🔍 Renew 按钮状态: visible={is_visible} enabled={is_enabled} disabled={is_disabled}")
            log(f"🔍 Renew 按钮 HTML: {btn_html!r}")
            # 检查 modal 元素是否存在
            modal_exists = page.evaluate("() => !!document.getElementById('renewService-221355')")
            log(f"🔍 modal #renewService-221355 存在: {modal_exists}")
            if modal_exists:
                modal_html = page.evaluate("() => document.getElementById('renewService-221355').outerHTML.slice(0, 800)")
                modal_visible = page.evaluate("() => { const m = document.getElementById('renewService-221355'); const s = window.getComputedStyle(m); return s.display !== 'none' && s.visibility !== 'hidden'; }")
                log(f"🔍 modal 可见: {modal_visible}")
                log(f"🔍 modal HTML: {modal_html!r}")
            # 检查 Flowbite 是否加载
            flowbite_loaded = page.evaluate("() => typeof window.Flowbite !== 'undefined' || typeof window.Modal !== 'undefined'")
            log(f"🔍 Flowbite/Modal JS 已加载: {flowbite_loaded}")
        except Exception as e:
            log(f"🔍 诊断失败: {e}")

        # 直接用 JS 打开 modal，绕过按钮点击（按钮点击在 headless 下可能不触发 Flowbite）
        try:
            opened = page.evaluate("""() => {
                const m = document.getElementById('renewService-221355');
                if (!m) return 'no-element';
                try {
                    // 尝试 Flowbite Modal API
                    if (typeof Modal !== 'undefined') {
                        const modal = new Modal(m);
                        modal.show();
                        return 'flowbite-api';
                    }
                } catch(e) {}
                // 兜底：手动去掉 hidden
                m.classList.remove('hidden');
                m.removeAttribute('aria-hidden');
                m.style.display = 'block';
                // 触发 Flowbite 的 backdrop
                document.body.classList.add('overflow-hidden');
                return 'manual-unhide';
            }""")
            log(f"🔍 直接打开 modal 结果: {opened}")
            time.sleep(2)
            # 检查 Create Invoice 是否可见
            try:
                create_btn.wait_for(state="visible", timeout=10000)
                log("✅ 直接打开 modal 成功（Create Invoice 可见）")
                modal_opened = True
                dump_modal_text(page)
            except Exception:
                log("⚠️ 直接打开后 Create Invoice 仍不可见，回落到按钮点击")
        except Exception as e:
            log(f"🔍 直接打开 modal 失败: {e}，回落到按钮点击")

        for i in range(3):

            # 若弹窗其实已经打开（上一轮点击生效但检测漏了），直接用，不重复点
            try:
                if create_btn.is_visible():
                    log("✅ 检测到 Create Invoice 已可见，弹窗其实已打开")
                    modal_opened = True
                    break
            except Exception:
                pass

            try:
                renew_btn.wait_for(state="visible", timeout=10000)
                renew_btn.scroll_into_view_if_needed()
                log(f"🖱️ 第 {i+1} 次尝试点击 'Renew'...")
                if i == 0:
                    renew_btn.click()
                    log("🖱️ 使用 Playwright 普通点击")
                elif i == 1:
                    # 第二次：JS 直接触发 click，绕过 actionability 检查
                    renew_btn.evaluate("el => el.click()")
                    log("🖱️ 使用 JavaScript 直接点击")
                else:
                    # 第三次：force 点击 + 分发鼠标事件
                    renew_btn.click(force=True)
                    log("🖱️ 使用 force 点击")

                # 等待一小段时间，检测是否出现"未到续期时间"弹窗
                time.sleep(2)
                page_text = page.locator("body").inner_text()
                if "Renewal Restricted" in page_text or "can only renew" in page_text.lower():
                    log("⚠️ 未到续期时间，无法续期。")
                    page.screenshot(path="renew_not_allowed.png")
                    return "NOT_TIME"  # 特殊状态

                handle_cloudflare(page)
                log("🖲️ 等待续期弹窗出现...")

                try:
                    # 不再硬编码弹窗标题（如 "Renew Plan"）：Create Invoice 按钮可见即判定弹窗已开。
                    # 标题文字一旦改版，旧写法会空等 30 秒×3 次然后失败（2026-10-10 run #15 就是这么挂的）。
                    create_btn.wait_for(state="visible", timeout=30000)
                    modal_opened = True
                    log("✅ 续期弹窗已出现（Create Invoice 可见）")
                    dump_modal_text(page)
                    break
                except Exception:
                    log("⚠️ 弹窗未出现（Create Invoice 不可见），把当前页面弹窗文本打到日志后重试...")
                    dump_modal_text(page)
                    time.sleep(2)

            except Exception as e:
                log(f"❌ 点击尝试出错: {e}")

        if not modal_opened:
            log("❌ 错误：尝试多次后，续费弹窗仍未出现。")
            page.screenshot(path="renew_modal_failed.png")
            return False

        # modal 已打开，先处理其中的 Turnstile 验证，再点 Create Invoice
        # （2026-10-10 run #19 教训：不先过验证直接点，会报 cf-turnstile-response required）
        # （2026-10-11 教训：被动等 30 秒不够，Turnstile iframe 需要主动触发）
        log("🛡️ 处理 modal 中的 Turnstile 验证...")
        handle_cloudflare(page)

        # 主动查找并触发 modal 内的 Turnstile widget
        try:
            turnstile_info = page.evaluate("""() => {
                const result = { iframes: 0, widgets: 0, token_present: false, token_len: 0 };
                // 查找所有 Turnstile 相关 iframe
                const iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
                result.iframes = iframes.length;
                // 查找 Turnstile widget 容器
                const widgets = document.querySelectorAll('.cf-turnstile');
                result.widgets = widgets.length;
                // 检查 token 字段
                const el = document.querySelector('[name="cf-turnstile-response"]');
                if (el) {
                    result.token_present = true;
                    result.token_len = (el.value || '').length;
                }
                return result;
            }""")
            log(f"🔍 Turnstile 状态: iframes={turnstile_info['iframes']}, widgets={turnstile_info['widgets']}, token_present={turnstile_info['token_present']}, token_len={turnstile_info['token_len']}")
        except Exception as e:
            log(f"🔍 Turnstile 状态检查失败: {e}")

        # 如果 widget div 存在但 iframe 没加载，尝试手动渲染 Turnstile
        try:
            need_render = page.evaluate("""() => {
                const widgets = document.querySelectorAll('.cf-turnstile');
                const iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
                return { widgets: widgets.length, iframes: iframes.length,
                         turnstile_loaded: typeof window.turnstile !== 'undefined' };
            }""")
            log(f"🔍 Turnstile 渲染检查: widgets={need_render['widgets']}, iframes={need_render['iframes']}, turnstile.js已加载={need_render['turnstile_loaded']}")
            if need_render['widgets'] > 0 and need_render['iframes'] == 0:
                if need_render['turnstile_loaded']:
                    log("🔄 widget 未渲染，尝试手动调用 turnstile.render()...")
                    render_result = page.evaluate("""() => {
                        const out = [];
                        document.querySelectorAll('.cf-turnstile').forEach((el, idx) => {
                            const info = {
                                idx: idx,
                                sitekey: el.getAttribute('data-sitekey') || '(无)',
                                action: el.getAttribute('data-action') || '(无)',
                                theme: el.getAttribute('data-theme') || '(无)',
                                size: el.getAttribute('data-size') || '(无)',
                                callback: el.getAttribute('data-callback') || '(无)',
                                hasWidgetId: !!el.dataset.widgetId,
                                outerHTML_slice: el.outerHTML.slice(0, 300)
                            };
                            try {
                                if (!el.dataset.tsRendered) {
                                    const wid = window.turnstile.render(el);
                                    el.dataset.tsRendered = '1';
                                    info.renderResult = 'OK, widgetId=' + wid;
                                } else {
                                    info.renderResult = '已渲染过，跳过';
                                }
                            } catch(e) {
                                info.renderResult = 'ERROR: ' + (e.message || e);
                            }
                            out.push(info);
                        });
                        return out;
                    }""")
                    for r in render_result:
                        log(f"🔍 widget[{r['idx']}]: sitekey={r['sitekey'][:20]}..., render={r['renderResult']}")
                        if r['sitekey'] == '(无)':
                            log(f"🔍 widget HTML: {r['outerHTML_slice']!r}")
                    time.sleep(5)
                    # 重新检查 iframe
                    recheck = page.evaluate("""() => document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]').length""")
                    log(f"🔍 手动渲染后 iframe 数量: {recheck}")
                else:
                    log("⚠️ turnstile.js 未加载，widget 无法渲染（可能是脚本被拦截）")
        except Exception as e:
            log(f"⚠️ Turnstile 手动渲染失败: {e}")

        # 如果有可见的 checkbox，主动点击
        try:
            ts_iframe_selector = 'iframe[src*="challenges.cloudflare.com"]'
            if page.locator(ts_iframe_selector).count() > 0:
                log("🖱️ 发现 Turnstile iframe，尝试主动触发...")
                for attempt in range(3):
                    try:
                        frame = page.frame_locator(ts_iframe_selector).first
                        # 尝试点击 checkbox 或整个 widget
                        checkbox = frame.locator('input[type="checkbox"]')
                        if checkbox.count() > 0 and checkbox.first.is_visible():
                            log(f"🖱️ 第 {attempt+1} 次点击 Turnstile checkbox...")
                            checkbox.first.click()
                            time.sleep(3)
                        else:
                            # 尝试点击 iframe body（适用于 invisible 模式）
                            log(f"🖱️ 第 {attempt+1} 次尝试点击 Turnstile 区域...")
                            page.locator(ts_iframe_selector).first.click()
                            time.sleep(3)
                    except Exception as e:
                        log(f"⚠️ Turnstile 点击尝试 {attempt+1} 失败: {e}")
                        time.sleep(2)
        except Exception as e:
            log(f"⚠️ Turnstile 主动触发失败: {e}")

        # 等待 Turnstile token 生成（cf-turnstile-response 隐藏字段有值）
        try:
            page.wait_for_function(
                "() => { const el = document.querySelector('[name=cf-turnstile-response]'); return el && el.value && el.value.length > 10; }",
                timeout=45000
            )
            log("✅ Turnstile token 已生成")
        except Exception:
            # 最后检查一次 token 状态
            try:
                final_check = page.evaluate("""() => {
                    const el = document.querySelector('[name="cf-turnstile-response"]');
                    return el ? (el.value || '').length : -1;
                }""")
                log(f"⚠️ Turnstile token 未检测到 (最终检查 token 长度: {final_check})，继续尝试点击（可能验证已自动通过）")
            except Exception:
                log("⚠️ Turnstile token 未检测到，继续尝试点击（可能验证已自动通过）")

        log("🖱️ 点击 'Create Invoice'...")
        # 点击前先关横幅（#41 的横幅拦截就是死在这里）
        dismiss_ncmp_banner(page)
        try:
            create_btn.scroll_into_view_if_needed(timeout=5000)
            create_btn.click(timeout=10000)
            log("🖱️ Playwright 点击已执行")
        except Exception as e:
            log(f"⚠️ Playwright 点击失败（可能仍被横幅拦截），关横幅后改用 force/JS 点击: {e}")
            dismiss_ncmp_banner(page)
            try:
                create_btn.click(timeout=10000, force=True)
                log("🖱️ force 点击已执行")
            except Exception:
                create_btn.evaluate("el => el.click()")
                log("🖱️ JS 点击已执行")

        # 点击后短暂等待，检查是否有验证错误信息
        time.sleep(3)
        try:
            page_text = page.locator("body").inner_text()
            if "cf-turnstile-response" in page_text.lower() or "field is required" in page_text.lower():
                log("❌ 检测到 Turnstile 验证错误：cf-turnstile-response field is required")
                page.screenshot(path="turnstile_error.png")
                # 尝试重新触发 Turnstile 再点一次
                log("🔄 尝试重新处理 Turnstile 后再次点击...")
                handle_cloudflare(page)
                time.sleep(5)
                try:
                    create_btn.evaluate("el => el.click()")
                    log("🖱️ 已重新点击 Create Invoice")
                except Exception as e2:
                    log(f"⚠️ 重新点击失败: {e2}")
        except Exception:
            pass

        # 点击后诊断：#42 的表现是点击后无跳转、按钮随后不可见，
        # 先把 URL 和 modal 状态打到日志，方便区分"横幅拦截"与"弹窗关闭"两种失败
        try:
            _modal_exists = page.evaluate("() => !!document.getElementById('renewService-221355')")
            log(f"🔍 点击后状态: url={page.url!r}, CreateInvoice可见={create_btn.is_visible()}, modal存在={_modal_exists}")
        except Exception as _e:
            log(f"🔍 点击后状态检查失败: {_e}")

        new_invoice_url = None
        start_wait = time.time()
        while time.time() - start_wait < 90:
            if "/payment/invoice/" in page.url:
                new_invoice_url = page.url
                log(f"🎉 页面已跳转: {new_invoice_url}")
                break
            if page.locator('iframe[src*="challenges.cloudflare.com"]').count() > 0:
                log("⚠️ 遇到拦截，尝试处理...")
                handle_cloudflare(page)
            time.sleep(1)

        if not new_invoice_url:
            log("❌ 未能进入发票页面，超时。")
            page.screenshot(path="renew_stuck_invoice.png")
            return False

        if page.url != new_invoice_url:
            page.goto(new_invoice_url)
        handle_cloudflare(page)

        log("🔎 查找 'Pay' 按钮...")
        pay_btn = page.locator('a:has-text("Pay"):visible, button:has-text("Pay"):visible').first
        pay_btn.wait_for(state="visible", timeout=30000)
        pay_btn.click()
        log("✅ 'Pay' 按钮已点击。")

        # 等待支付确认页面或跳转回服务页
        time.sleep(5)

        # 返回服务管理页面以获取新的到期时间
        page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        return True

    except Exception as e:
        log(f"❌ 续费异常: {e}")
        page.screenshot(path="renew_error.png")
        return False
def main():
    # 检查必要环境变量
    if not COOKIE_VALUE and not (EMAIL and PASSWORD):
        log("❌ 缺少登录凭证")
        sys.exit(1)

    global SERVICE_URL

    with sync_playwright() as p:
        try:
            if IS_PROXY:
                log(f"⚙️ 代理已启用: {PROXY_SERVER}")
            else:
                log("🌐 直连模式（未使用代理）")
            
            # 获取当前出口ip
            current_ip = get_current_ip(PROXY_SERVER)
            log(f"🎯 当前出口IP: {current_ip}")

            log("🚀 启动浏览器...")
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-infobars']
            )
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                proxy={"server": PROXY_SERVER} if IS_PROXY else None
            )
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            if not login(page):
                sys.exit(1)

            # 登录成功后，自动获取 Server ID
            server_id = get_server_id(page)
            if not server_id:
                log("❌ 无法获取 Server ID，退出。")
                sys.exit(1)
            SERVICE_URL = f"{BASE_URL}/service/{server_id}/manage"

            # 获取旧到期时间
            old_due = get_due_date(page)
            log(f"📆 续费前到期时间：{old_due}")

            # 执行续费
            renew_result = renew_service(page)

            new_due = old_due
            if renew_result == "NOT_TIME":
                log("⏳ 未到续期时间，目前无法续期")
                status = "⏳ 未到续期时间"
            elif renew_result is False:
                log("❌ 续费失败，脚本退出。")
                status = "❌ 续期失败"
            else:  # renew_result is True
                new_due = get_due_date(page)
                log(f"📆 续费后到期时间：{new_due}")
                status = "✅ 续期成功"

            # 发送 Telegram 通知
            send_telegram_notification(status, old_due, new_due)

            if renew_result == "NOT_TIME":
                sys.exit(0)
            elif renew_result is False:
                sys.exit(1)
            else:
                sys.exit(0)
        except Exception as e:
            log(f"❌ 浏览器启动出错: {e}")
            sys.exit(1)
        finally:
            if 'browser' in locals() and browser:
                browser.close()
                
if __name__ == "__main__":
    main()
