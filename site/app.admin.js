// 运营层：顶部数字滚动、手动抓取、信息源报告表单与提交。
Object.assign(APP_METHODS, {
    /* ---------- 顶部数字滚动动画（目标驱动）----------
     * displayTotal 始终向 this._countTarget 平滑靠拢；每加载一片数据就调用 bumpCount()，
     * 把目标抬到当前已加载真实条数，数字持续向上滚动（50 -> 1100 -> ... -> 3000+），
     * 不会中途定格成死值。任一时刻数字都代表「已加载的真实条数」。
     */
    startCountAnimation() {
      this._countFrom = 0;
      this._countSourceFrom = 0;
      this._countTarget = 0;
      this._countSourceTarget = 0;
      this._countStart = performance.now();
      this._countDur = 600;
      if (!this._countRAF) this._countRAF = requestAnimationFrame(this._countTick);
    },
    _countTick(now) {
      const t = Math.min((now - this._countStart) / this._countDur, 1);
      const e = 1 - Math.pow(1 - t, 3);
      const from = this._countFrom, to = this._countTarget;
      this.displayTotal = Math.round(from + e * (to - from));
      this.displaySource = Math.round(this._countSourceFrom + e * (this._countSourceTarget - this._countSourceFrom));
      if (t >= 1) {
        this.displayTotal = to;
        this.displaySource = this._countSourceTarget;
        this._countRAF = null;
        return;
      }
      this._countRAF = requestAnimationFrame(this._countTick);
    },
    bumpCount() {
      // 把滚动目标抬到当前已加载真实条数，并从当前显示值平滑接续；
      // 若 RAF 已停（动画定格），重新启动一段滚动；运行中则就地更新目标，无跳变。
      this._countFrom = this.displayTotal;
      this._countSourceFrom = this.displaySource;
      this._countTarget = this.totalCount;
      this._countSourceTarget = this.sourceNoticeCount;
      this._countStart = performance.now();
      this._countDur = 500;
      if (!this._countRAF) this._countRAF = requestAnimationFrame(this._countTick);
    },
    retryLoadFull() {
      // 重新加载全部分片：_mergeChunk 幂等（按 key 去重），已成功分片会命中浏览器缓存、
      // 重复合并也不会产生重复条目，失败的分片借此补上；已加载条数保留并显示，不回退到 50。
      this.dataStage = 'partial';
      this._loadStaticFull();
    },

    /* ---------- 触发后端抓取 ---------- */
    // 管理凭证（2026-09-26 审计 P1-2）：写接口需带 X-Admin-Token。凭证仅本机
    // （回环直连）可从 /api/admin/token 获取：403=服务端在但非本机（token-denied），
    // 404/网络错误=无后端（静态托管）→ 走下方降级链。进程内缓存一次。
    adminToken() {
      if (this._adminToken) return Promise.resolve(this._adminToken);
      return fetch('/api/admin/token', { cache: 'no-store' })
        .then(r => {
          if (r.status === 403) throw new Error('token-denied');
          if (!r.ok) throw new Error('no-backend');
          return r.json();
        })
        .then(j => { this._adminToken = j.token; return j.token; });
    },

    scrape() {
      this.scraping = true;
      let tokErr = '';
      this.adminToken()
        .catch(e => { tokErr = e.message; throw e; })
        .then(tok => fetch('/api/scrape', {
          method: 'POST', cache: 'no-store',
          headers: { 'X-Admin-Token': tok },
        }).then(r => r.json().then(j => ({ ok: r.ok, j }))))
        .then(({ ok, j }) => {
          if (ok && j.ok) {
            // 修复（2026-08-05 体检 严重-5）：此前先把 mtime 更新为抓取后的新值，
            // 再以 /api/lectures?since=<新mtime> 增量拉取——服务端比对 mtime 判定
            // unchanged 返回空数组，页面既不刷新也无成功提示。抓取后文件必然已变，
            // 直接全量加载并给出提示。
            this.loadLectures();
            this.showToast(j.message || '抓取完成');
          } else {
            this.showToast('抓取失败：' + ((j && j.message) || ''));
          }
        })
        .catch(() => {
          if (tokErr === 'token-denied') {
            this.showToast('手动抓取仅限本机使用（管理凭证只发给 127.0.0.1）');
            return;
          }
          // 静态托管（无后端）时的降级处理
          if (WORKFLOW_DISPATCH_URL) {
            fetch(WORKFLOW_DISPATCH_URL, { method: 'POST', cache: 'no-store' })
              .then(r => {
                if (r.ok) this.showToast('已触发后台更新，几分钟后刷新即可看到最新数据');
                else throw new Error('dispatch-failed');
              })
              .catch(() => this.showToast('立即更新触发失败，网站每天自动更新两次（约 11:00 与次日 01:00）'));
          } else {
            this.showToast('网站每天自动更新两次（约 11:00 与次日 01:00）；如需立即更新，请在本机运行爬虫或手动触发工作流');
          }
        })
        .finally(() => { this.scraping = false; });
    },

    /* ---------- 信息源报告（反馈栏目网址变更 / 长期无更新） ---------- */
    openReport() { this.showReport = true; },
    closeReport() { this.showReport = false; },

    _nowStr() {
      const d = new Date(), p = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    },

    // 结构化报告文本：既能粘贴进收集表，也能直接复制发给维护者
    buildReportText() {
      const f = this.reportForm;
      return [
        '【信息源报告】',
        '单位名称：' + (f.unit || '（未填）'),
        '所在校区：' + (f.campus || '（未填）'),
        '新的讲座栏目网址：' + (f.url || '（未填）'),
        '原栏目网址：' + (f.oldUrl || '（未填）'),
        '补充说明：' + (f.note || '（无）'),
        '联系方式：' + (f.contact || '（未填）'),
        '报告时间：' + this._nowStr(),
      ].join('\n');
    },

    // 剪贴板：优先 navigator.clipboard（仅在安全上下文 https / localhost 可用），
    // 被拒或不可用时回退 textarea + execCommand（局域网 http 直连场景）
    copyText(text) {
      const fallback = () => new Promise((resolve, reject) => {
        try {
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          const ok = document.execCommand('copy');
          document.body.removeChild(ta);
          ok ? resolve() : reject(new Error('execCommand-failed'));
        } catch (e) { reject(e); }
      });
      if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text).catch(fallback);
      }
      return fallback();
    },

    _reportFilled() {
      const f = this.reportForm;
      return !!(f.unit || '').trim() && !!(f.url || '').trim();
    },

    copyReport() {
      if (!this._reportFilled()) { this.showToast('请先填写「单位名称」和「新的讲座栏目网址」'); return; }
      this.copyText(this.buildReportText())
        .then(() => this.showToast('报告内容已复制到剪贴板'))
        .catch(() => this.showToast('复制失败，请手动选择文本复制'));
    },

    // 重置表单（含蜜罐）
    _resetReportForm() {
      this.reportForm = { unit: '', campus: '', url: '', oldUrl: '', note: '', contact: '', hp: '' };
    },

    // 站内提交：POST 到 Web3Forms，由其转发到维护者邮箱。
    // 任一环节失败（断网 / 被拦 / 额度用尽）→ 自动把内容复制好并保留弹窗，报告不会丢。
    async submitReport() {
      if (this.reportSending) return;
      if (!this._reportFilled()) { this.showToast('请先填写「单位名称」和「新的讲座栏目网址」'); return; }

      // 蜜罐命中：按成功处理，不给机器人反馈，也不消耗额度
      if ((this.reportForm.hp || '').trim()) {
        this.closeReport(); this._resetReportForm();
        this.showToast('报告已提交，感谢反馈！');
        return;
      }

      let last = 0;
      try { last = parseInt(localStorage.getItem(SRC_REPORT_TS) || '0', 10) || 0; } catch (e) { last = 0; }
      if (last && Date.now() - last < SRC_REPORT_GAP_MS) {
        this.showToast('刚刚已提交过一次，请稍后再试'); return;
      }

      const text = this.buildReportText();
      // 未配置通道：退化为复制（零配置仍可用）
      if (!REPORT_ACCESS_KEY) {
        try { await this.copyText(text); this.showToast('报告内容已复制，请发送给维护者'); }
        catch (e) { this.showToast('复制失败，请手动记录报告内容'); }
        return;
      }

      const f = this.reportForm;
      const payload = {
        access_key: REPORT_ACCESS_KEY,
        subject: '【信息源报告】' + (f.unit || '未填单位'),
        from_name: '木铎金声 · 讲座聚合站',
        botcheck: false,
        '单位名称': f.unit,
        '所在校区': f.campus || '不确定 / 不适用',
        '新的讲座栏目网址': f.url,
        '原栏目网址': f.oldUrl || '（未填）',
        '补充说明': f.note || '（无）',
        '联系方式': f.contact || '（未填）',
        '报告时间': this._nowStr(),
        '页面地址': location.href,
      };
      // 联系方式像邮箱时设为 replyto，便于直接回信确认
      if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test((f.contact || '').trim())) payload.replyto = f.contact.trim();

      this.reportSending = true;
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 12000);
      try {
        const res = await fetch(REPORT_ENDPOINT, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
          body: JSON.stringify(payload),
          signal: ctl.signal,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) throw new Error((data && data.message) || ('HTTP ' + res.status));
        try { localStorage.setItem(SRC_REPORT_TS, String(Date.now())); } catch (e) { /* 隐私模式忽略 */ }
        this.closeReport(); this._resetReportForm();
        this.showToast('报告已提交，感谢反馈！');
      } catch (e) {
        // 兜底：内容复制到剪贴板，弹窗留在原地，用户可直接发给维护者
        try { await this.copyText(text); } catch (e2) { /* 忽略 */ }
        this.showToast('提交失败，报告内容已复制，请发送给维护者', 4000);
      } finally {
        clearTimeout(timer);
        this.reportSending = false;
      }
    },
});
