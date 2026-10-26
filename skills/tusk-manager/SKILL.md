---
name: tusk-manager
description: Tusk のワークスペース管理 (tusk_manager)。作業の session 登録・中断・再開・巻き戻し・完了、Extension の導入・有効化、Context Cache の差分確認、ワークスペースの初期化を行う。Core の規約 (AGENTS.md・PROCESS_POLICY.md・EXTENSION_POLICY.md) が使うことを求める必須の道具なので、Tusk_Basic_skills は常に有効。
---

# tusk-manager — ワークスペース・session・Extension の管理

`scripts/tusk_manager.py` (標準ライブラリだけで動く) を、ワークスペースの root から呼ぶ。

```powershell
python extensions/Tusk_Basic_skills/skills/tusk-manager/scripts/tusk_manager.py --workspace . <サブコマンド> ...
```

## 発動条件

- 編集を始める前・止めるとき・終えるとき (Core `AGENTS.md`・`PROCESS_POLICY.md` の session 規約)。
- Extension を導入・有効化・無効化するとき (`EXTENSION_POLICY.md` §2)。
- 大きなスコープを読む前に、Context Cache で変更差分だけを確かめるとき (`EXTENSION_POLICY.md` §4)。

## 対象外

- 製品のビルド・試験・リリース。Core 以外の規約の正本 (規約の本文は Core の文書を読む)。

## サブコマンド

| サブコマンド | 用途 |
| :--- | :--- |
| `init [--core-source <path>]` | ワークスペースの `work/` 構成と初期設定を作る。`extensions/Tusk_Basic_skills` があれば有効一覧に入れる |
| `ext list / install <dir> / enable <name> / disable <name> / remove <name>` | Extension の一覧・導入・有効化 (manifest の SHA-256 検証あり)・無効化・削除。Tusk_Basic_skills は無効化・削除できない |
| `session start --work-id <id> --scope <paths...> [--owner <name>]` | 作業の開始スナップショットを取る。未完了 session とスコープが重なると拒否。scope はファイルのパス |
| `session stop --reason <理由> [--error-key <code>]` | 作業中の変更を保護して止める (`waiting_human`) |
| `session resume / revise [--scope ...] / rollback --to baseline|<generation> / complete / status / list [--stale-hours N] / abandon --reason ...` | 再開・世代を進めて続行・スコープ限定の巻き戻し・完了 (スコープ外汚染の確認)・状態・一覧・放棄 |
| `context --scope <scope_key> [--write]` | Context Cache の差分 (added / modified / unchanged / deleted) を見る。`--write` で更新 |
| `update` / `uninstall` | 更新の確認・一時ファイルの掃除 |

- 未完了の session が2つ以上あるときは、`stop/resume/revise/rollback/complete/status` に `--work-id <id>` が要る。
- 記録は `work/recovery/sessions/<work_id>.json` と `work/recovery/sessions.jsonl`。

## 検証

```powershell
python -m unittest discover -s extensions/Tusk_Basic_skills/skills/tusk-manager/tests -t extensions/Tusk_Basic_skills/skills/tusk-manager/tests
```

## 由来

2026-10-26 に Core (`Tusk_core/tools/tusk_manager.py`、複数 session 対応の作業中の版) から移した (所有者の決定)。
