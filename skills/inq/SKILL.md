---
name: inq
description: Tusk Incubation & Extension Harvesterスキル（/inq）。Focus Cache（地雷一覧）、Context Cache（構造・変更差分索引）、Session Recovery履歴（中断・手戻りログ）から使用頻度の高いアクション・スクリプト・頻出対策パターンを自動マイニングし、Skill化・Extension化の昇格ルートと実装ブループリント（行動方針）を組み立てる。マイニングと一次仕分けはすべてSkim役（低推論・高速）が担当し、本設計（Lead/Review役）が最終ブループリントを確定する。
---

# `/inq` — Incubation & Extension Harvester スキル

ワークスペース内に蓄積された **Focus Cache（地雷知識）・Context Cache（ファイル差分索引）・Session Recovery履歴（手戻りログ）** から、繰り返し使われているスクリプトや再発防止パターンを炙り出し、**Skill化・Extension化の昇格ルート（行動方針）** を組み立てる。

> [!IMPORTANT]
> - 旧InQの手動ステータス遷移儀式（`observed -> candidate -> verified ...`）は行わない。
> - **マイニング実行から3ルートへの一次仕分け（Route A / B / C）までは、すべて `role: skim`（流し見担当・低推論モデル）が一括で担当**し、トークンと時間を節約する。

---

## 役割分担と二段階フロー

自分の実行環境のプロファイル（`GEMINI_PROFILE.md` / `CLAUDE_PROFILE.md` / `CODEX_PROFILE.md`）に従い、以下の2ステージで実行する。

### Stage 1: 自動マイニング＆一次仕分け（担当: `role: skim` / `read_only`）
**Skim役がここまでをすべて完遂する：**

1. **ハーベスタースクリプトの実行**:
   ワークスペースのキャッシュとセッション履歴を自動走査し、頻出パターンとツール候補を抽出する。
   ```powershell
   python extensions/Tusk_Basic_skills/skills/inq/scripts/inq_harvester.py --workspace <対象ワークスペース> --json
   ```
2. **抽出シグナルの確認**:
   - **Focus Cache ホットスポット**: `発生回数 >= 2` の地雷、または同一 `対象` に集中している `正解パターン`（自動化チェッカー・Skill手順化の種）。
   - **Context Cache ユーティリティ候補**: `scripts/`, `tools/`, `utils/`, `helpers/` 等で追加・変更された再利用可能なCUIスクリプトや、複数箇所に散在する同名モジュール。
   - **Session Recovery ボトルネック**: `work/recovery/sessions.jsonl` で `stop`（中断）や `revise`（手戻り）が複数回発生しているファイル群・エラーコード。
3. **3つの昇格ルートへの一次仕分け**:
   Skim役は抽出した各候補を以下の3ルートへ分類し、引き継ぎテーブルを作成する：
   - **Route A (`Update Existing Skill`)**: 既存の `SKILL.md` や既存スクリプトにチェック項目・オプションを1つ足せば解決する小規模改善。
   - **Route B (`New Basic Skill`)**: 製品を問わず横断利用できる単機能CUIツール・検査スキル（`extensions/Tusk_Basic_skills/skills/<skill_id>/` への追加）。
   - **Route C (`New Extension: Tusk_<Name>`)**: 特定の製品ドメイン仕様、複数Skill、専用Focus Cacheを持つ独立パッケージ（`extensions/Tusk_<Name>/` の新設）。

---

### Stage 2: 昇格ブループリント（行動方針）の確定（担当: `role: lead` または `review`）
Stage 1（Skim役）が作成した一次仕分けテーブルと上位候補のファイルだけを読み込み、`Tusk_core/EXTENSION_POLICY.md` の制作5ステップに沿った **実行可能な行動方針（Blueprint）** を出力する。

---

## 出力フォーマット（InQ 昇格ブループリント）

```markdown
# InQ 昇格ブループリント（Extension / Skill 化行動方針）

- **対象ワークスペース**: `<workspace_path>`
- **マイニング集計**: 地雷記録 `<N>` 件 / スコープ変更 `<M>` 件 / セッション手戻り `<K>` 件

## 1. Skim一次仕分けサマリー (`role: skim`)
| 候補ID | 抽出元ソース | 対象ファイル・エラーキー | 頻度/スコア | 一次仕分けルート | 理由 |
|---|---|---|---|---|---|
| `CAND-01` | FocusCache / ContextCache / Session | `...` | `count=3` | **Route A / B / C** | ... |

## 2. 昇格ルート別・実装ブループリント (`role: lead/review`)

### [候補ID] `<タイトル>`（選択ルート: Route A / B / C）
- **配置先パス**: `extensions/Tusk_<Name>/...` または `extensions/Tusk_Basic_skills/skills/<skill_id>/`
- **パッケージ構成案**:
  - `SKILL.md` / `AGENTS.md`: 発動条件と入出力定義
  - `scripts/<tool>.py`: CUIインターフェース（引数・JSON出力契約）
- **元コードからの切り出し・統合手順**:
  1. `<既存パス>` から `<対象ロジック>` をCUIスクリプトとして抽出
  2. 旧箇所の重複コードを削除し、Skill/Extension呼び出しへ置換
  3. 最小独立動作テストと `EXTENSION-MANIFEST.json` の更新コマンド
- **推奨実装Intensity**: `LOW` / `MID` / `HIGH`
```
