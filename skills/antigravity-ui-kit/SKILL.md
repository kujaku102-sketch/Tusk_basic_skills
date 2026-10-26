---
name: antigravity-ui-kit
description: >-
  AntigravityスタイルのモダンダークUIデザインシステムおよびハンドメイドWebコンポーネント集。
  Webアプリ、Tauriデスクトップアプリ、ダッシュボードのUIをAntigravity風（ダークモード、グラスモーフィズム、Inter/JetBrains Mono、ステータスバッジ、ターミナルログ、カード、モーダル等）で構築する際に使用します。
---

# Antigravity UI Kit (WKZ BLACK) - Design System & Component Library

このスキルは、**Antigravity IDE 2.0（WKZ BLACK）** に準拠した高精度ダークUIコンポーネント集と設計ルールを提供します。
「**文字は無機質で小さく引き締まり、限りなくモノクロ（チャコールブラック）、アイコンやバッジ等の小物だけが高コントラストに発光する**」というプロフェッショナルな開発環境デザインです。

---

## 1. デザインシステム (Design Tokens: `WKZ BLACK`)

### カラーパレット (`style.css` / CSS 変数)

```css
:root,
[data-theme="wkz-black"] {
  /* 背景 (漆黒〜チャコールブラックのモノクロ階層) */
  --agy-bg-canvas: #101114;
  --agy-bg-surface: #141518;
  --agy-bg-card: #18191e;
  --agy-bg-card-hover: #22242a;
  --agy-bg-input: #121316;
  --agy-bg-input-focus: #1a1c22;

  /* 境界線 (細く繊細な1pxヘアライン) */
  --agy-border-subtle: #23252b;
  --agy-border-hover: #32353e;
  --agy-border-focus: #3b82f6;

  /* タイポグラフィ (無機質で小さめ・目に優しいオフホワイト) */
  --agy-text-primary: #d1d5db;
  --agy-text-secondary: #8b909a;
  --agy-text-muted: #585e6d;

  /* 高コントラスト小物・アクセント */
  --agy-cyan: #22d3ee;        /* アイコン・コードタグ */
  --agy-emerald: #10b981;     /* 正常・オンライン・成功 */
  --agy-amber: #f59e0b;       /* 警告・注意 */
  --agy-rose: #f43f5e;        /* エラー・危険操作 */
  --agy-blue: #3b82f6;        /* プライマリボタン */
  --agy-purple: #a855f7;      /* 特殊ステータス */

  /* フォント (Inter + JetBrains Mono) */
  --agy-font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --agy-font-mono: 'JetBrains Mono', 'Fira Code', Consolas, monospace;

  /* 角丸 (シャープ＆ミニマル) */
  --agy-radius-sm: 4px;
  --agy-radius-md: 6px;
  --agy-radius-lg: 8px;
}
```


---

## 2. コピペで使える主要パーツ一覧 (HTML & CSS)

### ① ヘッダー＆ステータスインジケータ (Header with Pulsing Dot)
```html
<header class="agy-header">
  <div class="agy-brand">
    <div class="agy-logo-glow">🦣</div>
    <div>
      <h1 class="agy-title">Tusk Tool</h1>
      <span class="agy-subtitle">Autonomous Workspace Agent</span>
    </div>
  </div>
  <div class="agy-header-actions">
    <div class="agy-badge agy-badge-online">
      <span class="agy-dot pulse"></span> ONLINE
    </div>
    <button class="agy-btn agy-btn-secondary">⚙️ 設定</button>
  </div>
</header>
```

### ② ステータスバッジ (Pill Badges)
```html
<!-- 成功 / 正常 -->
<span class="agy-badge agy-badge-success">● 正常 (Clean)</span>

<!-- 警告 / 変更あり -->
<span class="agy-badge agy-badge-warning">▲ 差分 3件</span>

<!-- エラー / 異常 -->
<span class="agy-badge agy-badge-danger">✕ 異常検出</span>

<!-- 処理中 -->
<span class="agy-badge agy-badge-loading">
  <span class="agy-spinner"></span> 処理中...
</span>
```

### ③ メトリクスカード (Stat Card Grid)
```html
<div class="agy-grid-4">
  <div class="agy-card">
    <span class="agy-card-label">管理拡張機能</span>
    <div class="agy-card-value text-blue">4 <span class="unit">件</span></div>
  </div>
  <div class="agy-card">
    <span class="agy-card-label">Focus Cache 地雷数</span>
    <div class="agy-card-value text-emerald">0 <span class="unit">件</span></div>
  </div>
</div>
```

### ④ ターミナル風コンソールログ (Console Log Panel)
```html
<div class="agy-card agy-console-card">
  <div class="agy-card-header">
    <h3>🖥️ 操作ログ (Console)</h3>
    <button class="agy-btn agy-btn-xs" onclick="clearLog()">クリア</button>
  </div>
  <div id="console-output" class="agy-console-body">
    <div class="agy-log system">[SYSTEM] Antigravity UI Kit initialized.</div>
    <div class="agy-log info">[INFO] Workspace ready at C:\Users\...</div>
    <div class="agy-log success">[SUCCESS] Manifest verified (SHA-256 matched).</div>
  </div>
</div>
```

### ⑤ モダンボタングループ (Button Variants)
```html
<!-- プライマリ（青グロー） -->
<button class="agy-btn agy-btn-primary">保存して適用</button>

<!-- セカンダリ（ダークアウトライン） -->
<button class="agy-btn agy-btn-secondary">キャンセル</button>

<!-- 危険操作（赤背景） -->
<button class="agy-btn agy-btn-danger">削除する</button>

<!-- ゴースト / ミニ -->
<button class="agy-btn agy-btn-ghost agy-btn-sm">再読み込み</button>
```

### ⑥ グラスモーフィック モーダル (Modal Dialog)
```html
<div id="modal-sample" class="agy-modal-overlay hidden">
  <div class="agy-modal-box">
    <div class="agy-modal-header">
      <h3>新規設定</h3>
      <button class="agy-modal-close" onclick="closeModal()">&times;</button>
    </div>
    <div class="agy-modal-body">
      <label class="agy-label">項目名</label>
      <input type="text" class="agy-input" placeholder="入力してください">
    </div>
    <div class="agy-modal-footer">
      <button class="agy-btn agy-btn-secondary" onclick="closeModal()">閉じる</button>
      <button class="agy-btn agy-btn-primary">決定</button>
    </div>
  </div>
</div>
```

---

## 3. アセット参照

全コンポーネントの完全な CSS とショーケース HTML は以下のリソースを参照してください：
- [`resources/agy-ui.css`](./resources/agy-ui.css) - すぐに組み込める完全なスタイルシート
- [`resources/showcase.html`](./resources/showcase.html) - 全パーツのライブデモ＆コピペカタログ
