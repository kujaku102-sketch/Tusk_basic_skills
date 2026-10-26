---
name: gamerseye
description: Target-Bound Autonomous Vision & ComputerUse Agent for Windows GUI applications (Tuskfolio, InDesign, Unity Editor, etc.) using Per-Monitor DPI V2, DWM invisible border stripping, Win32 SendInput, and Gemini/Claude multimodal models.
---

# GamersEYE (`/gamerseye`)
## 汎用ターゲット拘束型 ComputerUse ＆ リアルタイム視覚エージェント

GamersEYE は、Windows デスクトップ上の任意のGUIアプリケーション（Tuskfolio / `act3_view.exe`、InDesign、Unity Editor、ゲーム等）を **PID または ウィンドウ名正規表現** で捕捉し、安全に自律視覚・操作・UI検証を行うエージェントSkillです。
本フォルダ（`.agents/skills/gamerseye/`）単体で完全自己完結しており、他の環境やマシンへそのままコピーして動作します。

- **設計書**: `Tusk_architecture/docs/SPEC-UNIVERSAL-COMPUTER-USE-001.md`
- **自己完結モジュール**: [gamerseye_core/](gamerseye_core/)
- **スタンドアロンCLI**: [run.py](run.py)
- **依存一覧**: [requirements.txt](requirements.txt)
- **セキュリティ・アーキテクチャ**:
  1. **Per-Monitor DPI Awareness V2**: 125%/150% 等の拡大ディスプレイでも1ピクセルも狂わない物理座標系。
  2. **DWM 拡張枠 (DWMWA_EXTENDED_FRAME_BOUNDS)**: Windows 10/11 の不可視8pxシャドウ枠を完全除外し、ウィンドウ外やデスクトップへの誤爆をOSレイヤーで物理遮断。
  3. **相対座標 (0..1000 または rel_x, rel_y) クランプ**: 操作対象ウィンドウの枠内のみに入力を強制制限。
  4. **Win32 SendInput (Unicode対応)**: 日本語入力・記号・ショートカットキーの完全対応。
  5. **ゼロディスク即時視覚**: メモリ上（mss / Pillow）でウィンドウ矩形を瞬発クロップし、LLMに高速供給。
  6. **APIキー自動フォールバック**: 環境変数 `GEMINI_API_KEY` / `GOOGLE_API_KEY` に加え、`work/settings/gemini_api_key.txt` からも自動検出。

---

## 使い方 (Quick Start)

### 1. ターゲットウィンドウの吸着テスト (Dry-Run / 座標・矩形検証)
ターゲットアプリ（例: Tuskfolio）を起動した状態で、ウィンドウを捕捉して可視矩形とキャプチャが正確か確認します（操作は行われません）。

```powershell
python .agents/skills/gamerseye/run.py --title "TuskFolio" --dry-run
```

PIDで指定する場合:
```powershell
python .agents/skills/gamerseye/run.py --pid <PID> --dry-run
```

### 2. 自律操作 ＆ UI操作マトリクス生成
Gemini 4 Argon / Flash などのマルチモーダルモデルを用いて自律的にUIを巡回し、操作経路マップ (`UI_OPERATION_MATRIX.md`) を作成させます。

```powershell
python .agents/skills/gamerseye/run.py --title "TuskFolio" --goal "全メニューを走査してUI操作マトリクスを作成せよ" --max-steps 15
```

---

## 主な引数オプション
- `--title <REGEX>`: ウィンドウタイトルの正規表現（例: `"TuskFolio"`, `"InDesign"`）
- `--pid <PID>`: プロセスID（直接指定）
- `--goal <TEXT>`: エージェントに達成させる目的・検証タスク
- `--model <NAME>`: 使用するモデル（既定: `gemini-3.8-flash`。2.5-flash は 404）
- `--max-steps <N>`: 最大実行ステップ数（既定: 20）
- `--dry-run`: 操作を行わず、ウィンドウ捕捉とキャプチャのみ確認
- `--report <PATH>`: 結果マトリクス出力先（既定: `work/UI_OPERATION_MATRIX.md`）

### 3. 15FPS 連射疑似動画視覚 (NEW)
ゲーム、UIアニメーション、ホバー・ドラッグの追従確認用の高精度15FPSバースト視覚機能（ゼロディスクI/O RAM保持）。

```powershell
# ① -uicheck: マウスカーソル追従 256x256 クロップ連射 (デフォルト1.5秒 = 約23フレーム)
python .agents/skills/gamerseye/run.py --title "TuskFolio" -uicheck --duration 1.5 --fps 15 [--save-anim uicheck.webp]

# ② -fullview: ウィンドウ全体 15FPS 連射 (ゲーム・動画確認用)
python .agents/skills/gamerseye/run.py --title "TuskFolio" -fullview --duration 1.5 --fps 15 [--save-anim fullview.webp]
```

- `-uicheck`: マウスカーソル中心の 256×256 領域を追従連射
- `-fullview`: ウィンドウ全体を 15FPS 連射
- `--fps <N>`: 連射FPS（既定: 15）
- `--duration <SEC>`: 連射秒数（既定: 1.5秒）
- `--save-anim <PATH>`: 任意で Animated WebP / GIF として保存（省略時はRAMバッファのみ）

---

## GUI 試験を Antigravity (`agy`) へ受け渡す (`--handoff`)
15FPS 連射や自律操作を Claude に見せるとトークンが重い。操作・撮影は Gemini Flash(agy)に任せ、Claude は返ったキャプチャ1〜2枚で最終判定だけする。

**役割分担**
- Gemini/Antigravity = 操作・連射・動きの確認・項目ごとの一次判定。
- Claude = 返った JSON の `evidence` の PNG を1〜2枚だけ開いて最終の合否。Gemini の要約は捏造があり得る。必ずキャプチャで裏取りする。
- 根拠PNGが実在しない項目・根拠無しの pass は、ハーネスが自動で unknown に落として `anomalies` に書く。

**使い方**
```powershell
python .agents/skills/gamerseye/run.py --handoff spec.json
```
spec.json:
```json
{"target": {"exe": "C:/.../act3_view.exe"},
 "place": "40,40,1200,800",
 "document": "任意: 開く文書のパス",
 "items": [{"id": "menu_open", "check": "ファイルメニューを開くと一覧が出る"}],
 "out_dir": "キャプチャ保存先(PNG)", "timeout": 300}
```
- `target`: `exe`(ハーネスが起動して配置) か `pid`(起動済みの窓)。`title` だけでは捕まえない(同じ名前の他人の窓を掴んで閉じないため)。agy にも `--pid` だけを渡し、`--close-window` は `--pid` が要る。
- 呼び出し: `agy -p … --model gemini-3.8-flash-medium --output-format json --json-schema … --sandbox --add-dir … --print-timeout <秒>s`。`--dangerously-skip-permissions` は使わない。
- モデルは Flash のみ(`gemini-3.8-flash-{low,medium,high}`。spec の `model` か環境変数 `GAMERSEYE_AGY_MODEL`)。他は拒否。
- 返り(標準出力と `out_dir/handoff_result.json`): `items[{id,result(pass|fail|unknown),evidence[],note}]`、`steps`、`captures`、`anomalies`。生出力は `out_dir/agy_raw.txt`。

**agy が使う道具(同じ run.py)**
`--shot out.png`(1枚撮る。Claude が最終判定で見る用にも使う) / `--burst DIR [--duration 1.5 --fps 15]`(連射をPNG保存) / `--place X,Y,W,H` / `--click X,Y` / `--key 名前` / `--type 文字列` / `--close-window` / `--launch EXE [--open DOC]`。窓は `--pid` か `--title` で捕捉。

**決まり**
- 保存は写し(複製)にだけ。正本は別。
- 窓の位置とサイズを先に決める(起動直後 1280x960 は下端がタスクバーに隠れる)。
- 閉じるのは閉じるボタン相当(`--close-window`)。Alt+F4 は安全装置で止まる。agy が閉じ忘れたらハーネスが閉じる。
- 試験文書のページ数制限は無し(所有者指示)。
- アプリはハーネスが agy の外で起動する(agy の sandbox 内で起動すると命令の終了で落ちる)。agy の exebox デスクトップは対象窓の居る Default デスクトップへ付け替えて操作する(binder が自動で行う)。
- 単体試験: `python -m unittest discover -s tests`(agy 呼び出しはモック)。
- **時間切れ**: agy は `--print-timeout` 切れでも status=SUCCESS・response 空で返す。stderr の "print timeout" を検出して `timed_out: true` と anomalies に記録し、conversation_id で1回だけ回収(90秒)する。取れなければ全項目 unknown(`recovered` で区別)。
- **時間上限と分割**: `timeout` 省略時は項目数×150秒(最低300秒)。項目が `chunk`(既定6)を超えると複数回に分けて実行(`exe` 指定のみ。毎回起動し直す。キャプチャは `out_dir/partN/`)。プロンプトで撮影は項目あたり1〜2枚に絞らせる(トークン節約。1回18〜28万トークンかかる)。
- **終了処理**: WM_CLOSE 後 5秒待ち、handoff 自身が起動した PID が残っていればその PID だけ強制終了(エラー小窓・未保存確認対策)。`pid` 指定で渡された他人の窓は触らない。
