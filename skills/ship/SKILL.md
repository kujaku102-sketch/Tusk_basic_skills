---
name: ship
description: 出荷・受け渡し前検疫＆パッケージ整理スキル（/ship）。開発中に溜まったゴミファイル（.bak, scratch/, 散在ログ・画像）、PC固有のハードコード絶対パス（C:\Users\...）、秘密情報の混入を一括検疫し、マニフェスト（SHA-256）の最新化と受け渡しサマリー（SHIP_READINESS.md）の生成をワンストップで実行する。一次スキャンはすべてSkim役が担当する。
---

# `/ship` — 出荷・受け渡し前検疫＆リリース整理スキル

開発中に蓄積した一時ファイル・実験残骸・PC固有のハードコードパス・マニフェストずれを、**出荷・別環境への受け渡し（Handoff）直前に一括で検疫・掃除・ロックする**。

> [!TIP]
> 開発中は細かい整理を気にせず高速に実装・実験を回し、マイルストーン完了時や他チーム（Codex等）への受け渡し時に `/ship <対象パス>` を1回実行するだけで、リリース可能なクリーン状態へ仕上げられる。

---

## 検疫・整理の4大ステップ

### Step 1: ポータビリティ＆機密検疫 (`Portability & Secret Quarantine`)
- **PC固有絶対パスの検出**:
  - 本番コード（`.py`, `.rs`, `.ts`, `.js`, `.json`, `.toml`, `.ps1`, `.bat`）の中に残った `C:\Users\...` や `/Users/...`、`D:\...` 等の環境依存ハードコードパスを行番号付きで全件炙り出す（別PCやCIへ渡した瞬間に動かなくなる最大原因を排除）。
- **秘密情報・認証キーの検出**:
  - `api_key`, `secret`, `token`, `Bearer `, 秘密鍵ファイル（`.pem`, `.key`, `credentials.json`, `.env`）の混入を検出する。

### Step 2: 開発ゴミ・実験残骸の検出と隔離 (`Cruft & Scratch Sweep`)
- 以下の開発中ゴミ・バックアップ残骸を検出する：
  - バックアップ・一時ファイル: `*.bak*`, `*.tmp`, `*.orig`, `*.swp`, `*~`
  - 散在スクラッチ・ログ: `scratch/`, `tmp_*`, `crash.log`, `untranslated.log`, `runtime_copy.log`
  - ルート直下に放置されたテスト用画像・ダンプ: `step_*.png`, `recon_*.png`, `*_test.folio`
  - Pythonバイトコードキャッシュ: `__pycache__/`, `*.pyc`, `.pytest_cache/`
- `--clean-cache` 指定時は `__pycache__` / `*.pyc` / `.tmp` 等の安全なキャッシュだけを即座に削除し、判断が必要な `.bak*` や `scratch/` は削除候補リストとして提示する（無断で製品データを破壊しない）。

### Step 3: マニフェスト（SHA-256）の自動再計算 (`Manifest Lock`)
- 対象ディレクトリに `DISTRIBUTION-MANIFEST.json` または `EXTENSION-MANIFEST.json` が存在する場合、`--update-manifest` 指定で管理対象ファイルの SHA-256 を一括再計算してロックする。

### Step 4: 受け渡し準備レポートの生成 (`Ship Readiness Report`)
- 検疫結果（Blocker / Warning / Clean）と、受け渡し先が最初に読むべきエントリポイント・ビルド/テストコマンドをまとめたレポートを出力する。

---

## 二段階実行フロー（Skim → 修正・確定）

### Stage 1: 自動検疫スキャン (`role: skim` / 低推論・高速)
付属のCUIインスペクタを実行し、対象ディレクトリの検疫結果をJSONで取得する。

```powershell
# 1. 検疫スキャン（読み取り専用・Dry-run）
python extensions/Tusk_Basic_skills/skills/ship/scripts/ship_inspector.py --target <対象パス> --json

# 2. 安全なキャッシュ掃除＋マニフェストSHA更新を同時実行する場合
python extensions/Tusk_Basic_skills/skills/ship/scripts/ship_inspector.py --target <対象パス> --clean-cache --update-manifest --json
```

### Stage 2: ブロッカー解消＆出荷判定 (`role: lead` / `implementation`)
1. Stage 1 で **`BLOCKER`**（本番コード内のPC固有絶対パス、秘密情報混入、`.bak` 放置）が出た場合のみ、該当箇所を相対パス／環境変数へ置換し、不要な `.bak` や `scratch/` を整理する。
2. 再度 `ship_inspector.py --update-manifest` を回して `readiness: "READY"` を確認し、受け渡しレポートを提示する。

---

## 出力レポート形式

```markdown
# Ship Readiness Report（出荷・受け渡し検疫レポート）

- **対象パス**: `<target_path>`
- **出荷判定**: `[READY / NEEDS_CLEANUP / BLOCKED]`
- **マニフェスト状態**: `up_to_date`（管理ファイル `<N>` 件）

## 1. 検疫サマリー
| 検査項目 | 件数 | 判定 | 内容 |
|---|---|---|---|
| 1. 本番コード内のPC固有絶対パス | `<N>` 件 | PASS / BLOCKED | `C:\Users\...` 等のハードコード |
| 2. 秘密情報・認証ファイル混入 | `<N>` 件 | PASS / BLOCKED | `.env`, APIキー等 |
| 3. バックアップ・スクラッチ残骸 | `<N>` 件 | PASS / WARNING | `*.bak*`, `scratch/`, 散在ログ |
| 4. キャッシュ・一時ファイル | `<N>` 件 | PASS / CLEANED | `__pycache__`, `*.tmp` |

## 2. 要対応リスト（存在する場合のみ）
| 種別 | ファイル・行番号 | 検出内容 | 推奨アクション |
|---|---|---|---|
| HardcodedPath / Cruft / Secret | `[file](file:///...)` | ... | 相対パス化 / 削除 / `.gitignore` 追加 |
```
