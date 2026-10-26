---
name: tuskbatch
description: Gemini Batch API / Vertex AI ($300クレジット枠) / GCEローカルLLM (Gemma 4等) 3刀流の定型コード改修・一括処理スキル（/tuskbatch）。設計図（tasks.json）からライブソースを抽出して構造化JSONパッチを生成し、GCEリモート中継・自動シャットダウン保護・パッチ自動適用・テスト検証（失敗時ロールバック）までをワンストップで実行する。
---

# `/tuskbatch` — 3刀流ハイブリッド一括改修＆GCEリモートフォージ

設計図（`tasks.json`：対象ファイル・行レンジ・改修要件）さえ書けば、**同じコマンド体系のまま以下の3つの推論エンジンと GCE SSH リモート中継を自在に切り替えられる**定型スキル。

---

## 選べる3大バックエンド（`--backend`）

| バックエンド | 指定フラグ | 特徴・使いどころ |
| :--- | :--- | :--- |
| **1. Vertex AI モード**<br>（GCP クレジット消化） | `--backend vertex`<br>（デフォルトモデル: `gemini-4-argon`） | **GCP の \$300 クレジット残高から直接引き落とし**。GCS バケット不要で並列 Structured Output 推論を回し、即座に `batch_results.jsonl` を生成する。 |
| **2. GCE ローカルLLM モード**<br>（Ollama / vLLM） | `--backend local-llm`<br>（デフォルトモデル: `gemma4:26b`） | GCE GPU インスタンス等で動かしている **Gemma 4（`gemma4:26b` MoE / `gemma4:31b`）や Qwen2.5-Coder** の OpenAI 互換エンドポイントへ投げる。API 従量課金ゼロ。 |
| **3. Gemini Batch API モード**<br>（AI Studio 50% OFF） | `--backend gemini-batch`<br>（デフォルトモデル: `gemini-4-argon`） | Google 側の非同期バッチキュー（`client.batches.create`）へ投入。クレジット失効後も **通常の半額（50% OFF）** で大量タスクを寝かせて処理できる。 |

---

## 使い方（CUIコマンド）

### A. ローカルから直接実行（ワンショット）

```powershell
# 1. GCP $300クレジット（Vertex AI + gemini-4-argon）で一括実行＆パッチ適用＋cargo test検証
python extensions/Tusk_Basic_skills/skills/tuskbatch/scripts/tusk_batch_runner.py run --spec tasks.json --backend vertex --project <GCP_PROJECT_ID>

# 2. GCE上のローカルLLM（Ollama gemma4:26b）へ向けて一括実行
python extensions/Tusk_Basic_skills/skills/tuskbatch/scripts/tusk_batch_runner.py run --spec tasks.json --backend local-llm --base-url http://<GCE_IP>:11434/v1 --model gemma4:26b

# 3. Gemini Batch API（50% OFF 非同期キュー）で実行
python extensions/Tusk_Basic_skills/skills/tuskbatch/scripts/tusk_batch_runner.py run --spec tasks.json --backend gemini-batch
```

### B. GCE インスタンスを「24時間リモートフォージ」として使う（PC電源オフ対応）

付属の `gce_forge_setup.sh` により、**指定分数後（デフォルト180分）の自動シャットダウン保護タイマー（課金暴走防止）** 付きで Ollama（`gemma4:26b`）・Python SDK・Rust 環境を一発構築できる。

```powershell
# Step 0: GCEインスタンスの初期構築＋180分後自動停止タイマー設定（初回または起動時）
python "...\tusk_batch_runner.py" remote-setup --host <GCEインスタンス名> --zone <ゾーン> --shutdown-mins 180 --llm-model gemma4:26b

# Step 1: ローカルで tasks.json からスライスを切り出し、GCEへ転送して nohup バックグラウンド実行開始
# （※ これを叩いたらローカルWindows PCは電源を切ってOK！）
python "...\tusk_batch_runner.py" remote-run --spec tasks.json --host <GCEインスタンス名> --zone <ゾーン> --backend vertex

# Step 2: GCE上の進行状況・ログ確認
python "...\tusk_batch_runner.py" remote-status --host <GCEインスタンス名> --zone <ゾーン>

# Step 3: 完了した結果をGCEから回収し、ローカルコードへパッチ適用＋cargo test自動検証（失敗時自動ロールバック）
python "...\tusk_batch_runner.py" remote-pull --work-dir <作業フォルダ> --host <GCEインスタンス名> --zone <ゾーン> --verify
```

---

## 設計図（`tasks.json`）の定型フォーマット

テンプレートは `resources/example_tasks.json` を参照。

```json
{
  "project_root": ".",
  "backend": "vertex",
  "model": "gemini-4-argon",
  "location": "us-central1",
  "verify_cmd": "cargo test",
  "temperature": 0.1,
  "tasks": [
    {
      "id": "task_1_preserve_cell_padding_valign",
      "title": "Preserve Inferred Cell padding_pt and valign in elastic.rs",
      "requirement": "Modify the loop in synchronize_table_rows_and_pushdown so cell.padding_pt is only populated via get_or_insert([2.5, 3.0, 2.5, 2.2]), and preserve existing cell.valign.",
      "slices": [
        {
          "file": "crates/act4_core/src/elastic.rs",
          "start_line": 2015,
          "end_line": 2040,
          "lang": "rust"
        }
      ]
    }
  ]
}
```
