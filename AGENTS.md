<!-- md-scope-document: EXTENSION -->
# Tusk_Basic_skills (汎用スキル拡張パッケージ)

このExtensionはプロジェクト横断で利用する汎用Skill群を提供する。

**常に有効 (Core 必須)**: Core の規約が使う `tusk_manager` (`skills/tusk-manager/`) を収録しているので、このExtensionは無効化・削除しない (`tusk_manager` も拒否する)。

## 収録Skill一覧（必要な時だけ該当 `SKILL.md` を読むこと）

0. **tusk-manager — ワークスペース・session・Extension の管理 (`skills/tusk-manager/SKILL.md`)**
   - **用途**: 作業 session の登録・中断・再開・巻き戻し・完了、Extension の導入・有効化、Context Cache の差分確認、ワークスペースの初期化。実体は `skills/tusk-manager/scripts/tusk_manager.py`。
   - **発動条件**: 編集の開始・中断・完了 (Core の session 規約)、Extension の導入・有効化、大きなスコープの差分確認を行う時。

1. **`/tuskfact` — Tusk製品アーキテクチャ適合監査 (`skills/tuskfact/SKILL.md`)**
   - **用途**: 対象製品が「1. エラーコード規則 (`1XX-5XX`固定＋`6XX-9XX`製品自由)」「2. 極小ランタイム＋モジュール呼び出し型のマイクロカーネル思想」「3. CUIベース＋GUIラッパーの完全分離駆動」の3原則に従っているかを、Skim（流し見）→ 本レビューの二段階で監査し、レポートと改善箇所リストを出力する。
   - **発動条件**: `/tuskfact` が指示された時、または製品・Extensionの設計監査・リファクタリング診断を行う時。

2. **`/inq` — Incubation & Extension Harvester (`skills/inq/SKILL.md`)**
   - **用途**: Focus Cache（地雷一覧）、Context Cache（変更差分・構造索引）、Session Recovery履歴（手戻りログ）から頻出アクションや再利用ツール候補を自動マイニングし、Skill化・Extension化の昇格ルート（Route A/B/C）と実装ブループリント（行動方針）を組み立てる。マイニングから一次仕分けまではすべて `role: skim` が担当する。
   - **発動条件**: `/inq` が指示された時、または散在するスクリプト・頻出対策をSkill/Extensionへ昇格・整理する時。

3. **`/ship` — 出荷・受け渡し前検疫＆リリース整理 (`skills/ship/SKILL.md`)**
   - **用途**: 開発中に溜まったゴミファイル（`.bak`, `scratch/`, 散在ログ）、本番コード内のPC固有ハードコード絶対パス（`C:\Users\...`）、秘密情報の混入を一括検疫し、キャッシュ掃除とマニフェスト（SHA-256）の最新化をワンストップで実行する。
   - **発動条件**: `/ship` が指示された時、または他チームへの受け渡し・リリースビルド作成・パッケージ確定を行う時。

4. **GamersEYE (`skills/gamerseye/SKILL.md`)**
   - **用途**: Windows GUIアプリケーション（Tuskfolio, InDesign, Unity, Tauri等）の自律操作・ウィンドウバインド・DPI補正キャプチャ・マルチモーダル視覚検査。
   - **発動条件**: 実機GUIの自動操作、UIスモークテスト、画面キャプチャ検証を行う時。

5. **FiumLLER Vision (`skills/fiumller-vision/SKILL.md`)**
   - **用途**: PDF または `.folio` ページの外観レンダリング、テキストフレーム溢れ・重なり等の幾何異常検知、2つのPDF間の視覚差分比較。
   - **発動条件**: DTP出力結果やPDF/組版データの視覚・幾何QAを行う時。

6. **Antigravity UI Kit (`skills/antigravity-ui-kit/SKILL.md`)**
   - **用途**: AntigravityスタイルのモダンダークUIデザインシステム（グラスモーフィズム、バッジ、ターミナルログ、カード等）のCSSとHTMLショーケース。
   - **発動条件**: Webアプリ、Tauriデスクトップアプリ、ダッシュボードUIを構築・改修する時。

7. **`/tuskbatch` — Gemini Batch API 定型コード改修・一括処理 (`skills/tuskbatch/SKILL.md`)**
   - **用途**: 設計図（`tasks.json`：対象ファイル・行レンジ・改修要件）からライブソースを切り出して Structured Output（JSONパッチスキーマ）付きリクエストJSONLを自動生成し、`gemini-4-argon` Batch API への投入・ポーリング・結果DL・パッチ自動適用・テスト検証（失敗時自動ロールバック）までをワンストップで実行する。
   - **発動条件**: `/tuskbatch` が指示された時、または複数のリファクタリング・改修タスクを Gemini Batch API に一括投入して処理する時。

## 運用規則
- 作業に関係のない Skill の `SKILL.md` や `references/`、`scripts/` を事前読込しない。
- 各 Skill の実行で発見した共通の地雷・再発防止策は `focus_cache/LANDMINES.md` に記録する。
