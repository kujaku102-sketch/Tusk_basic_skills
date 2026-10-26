---
name: fiumller-vision
description: PDF / .folio 幾何・外観検査スキル（/fvision）。FiumLLER/PDFiumレンダリング、Act3 View実GUI撮影、原本対比の差分可視化（diff）、UDR幾何アノマリー（重なり・はみ出し・異常配置）検出をワンストップで実行する。Skim役（低推論）による高速レンダリング・差分検出と、Lead/Review役による視覚品質判定の二段階で実行する。
---

# `/fvision` — FiumLLER Vision & Geometry Inspection スキル

PDFの紙面および `.folio` の実GUIについて、**PDFium（FiumLLER CLI）高精度レンダリング・原本差分ヒートマップ（diff）・幾何アノマリー自動検出** をワンストップで実行し、組版崩れやレイアウト欠陥を機械＋視覚の二重チェックで検証する。

> [!IMPORTANT]
> 機械スコアや終了コードだけで「合格」と判定しない。生成されたレンダリング画像（または差分画像）を必ず画像ツールで視認し、確認したページ・倍率・領域を明記して報告する。

---

## 1. コマンド構文とフラグ仕様

チャット上で `/fvision` が指示された場合、以下の構文とフラグを解釈して検査パイプラインを実行する：

```text
/fvision <対象PDFまたは.folio> [-page <番号>] [-compare <原本PDF>] [-diff] [-anomalies] [-geometry] [-dpi <数値>] [-out <出力パス>] [-legacy-rules]
```

| フラグ | 意味・動作 |
|---|---|
| `<対象PDFまたは.folio>` | 検査対象のファイルパス（必須） |
| **`-page <番号>` / `-p`** | 検査対象の1-basedページ番号（省略時は `1`） |
| **`-compare <原本PDF>` / `-c`** | 対比元の原本PDFパス。指定時は同一ページの視覚差分を生成 |
| **`-diff`** | 原本との差分ヒートマップ画像を生成して出力する |
| **`-anomalies`** | UDR幾何アノマリー（フレーム重複、境界はみ出し等）を自動検出する |
| **`-geometry`** | 指定ページのUDR幾何構造（BBox、テキスト、スタイル）をJSON形式でダンプする |
| **`-dpi <数値>`** | レンダリング解像度（DPI、既定値: `150.0`） |
| **`-out <出力パス>` / `-o`** | 画像およびレポートの出力先ディレクトリ（既定値: `work/fvision_output/`） |
| **`-legacy-rules`** | 旧文書固有の15ptマージン、CAD帯、目次専用ヒューリスティクスを有効化 |

---

## 2. 検査の4大機能

### 1. 高精度レンダリング (`render_inspect.py`)
- **PDF紙面描画**: FiumLLER CLI（PDFium）により、ページ回転（90°/180°/270°）を正しく反映した物理座標系で指定ページを描画。
- **.folio 実GUI描画**: `act3_view.exe`（仮想非アクティブデスクトップ `--inactive-desktop` 含む）により、ヘッドレス環境でもGUI実描画スクリーンショットを取得。

### 2. 原本対比・視覚差分ヒートマップ (`compare_pages.py`)
- 原本PDFと訳文/再組版PDFを同一DPIでレンダリングし、Pillowによるピクセル差分を赤色ヒートマップとして可視化。
- 原語と翻訳言語の文字差異を過剰にエラー扱いせず、**「図面の位置ズレ」「ヘッダー/フッターの消失」「テーブル枠の崩壊」** を直感的に炙り出す。

### 3. 幾何アノマリー自動検出 (`detect_anomalies.py`)
- Act4.1 のUDR抽出結果を走査し、以下のアノマリーを機械検出：
  - **フレーム衝突・重なり（Overlap）**: 同一階層のテキスト枠や図面枠の不正な交差。
  - **マージン逸脱（Out of Bounds）**: 用紙境界（TrimBox）からの文字・要素のはみ出し。
  - **ゼロ幅・反転座標（Degenerate BBox）**: 幅や高さが0以下の不正なジオメトリ。

### 4. 幾何構造ダンプ (`inspect_geometry.py`)
- 指定ページのフレーム階層・物理座標・フォントサイズ・割り当てアセットキーを構造化JSONとして出力。

---

## 3. 二段階実行フロー（Skim → 視覚判定）

自分の実行環境のプロファイル（`GEMINI_PROFILE.md` / `CLAUDE_PROFILE.md` / `CODEX_PROFILE.md`）に従い、以下の2ステージで実行する。

### Stage 1: 自動検査＆レンダリング実行 (`role: skim` / 低推論・高速)
統合ランナー `scripts/fvision_runner.py` を実行し、描画画像・差分ヒートマップ・幾何アノマリーJSONを一括生成する。

```powershell
# 基本実行: ページ描画＋アノマリー検査
python extensions/Tusk_Basic_skills/skills/fiumller-vision/scripts/fvision_runner.py "work/output.pdf" -p 14 --anomalies

# 原本対比: 差分ヒートマップ生成
python extensions/Tusk_Basic_skills/skills/fvision_runner.py "work/output.pdf" -p 14 --compare "origin.pdf" --diff

# JSON構造化出力（自動パイプライン連携用）
python extensions/Tusk_Basic_skills/skills/fiumller-vision/scripts/fvision_runner.py "work/output.pdf" -p 14 --anomalies --json
```

### Stage 2: 視覚品質判定 ＆ レポート作成 (`role: lead` / 本エージェント)
1. 生成された画像（`rendered_image` / `diff_image`）を画像ツールで確認。
2. アノマリー検出結果（`anomalies`）と照合し、真の組版崩れ（True Positive）か許容される翻訳差異（False Positive）かを判定。
3. 画像リンク（`![caption](/C:/...)` と `file:///C:/...`）を添えた検査レポートを出力する。

---

## 4. 実行時リソース・フォールバック
- `act4.exe`、`act3_view.exe`、`fiumller.exe` は `Act4.1/target/release/`、`Act3/target/release/`、`bin/` から自動検出される。
- 個別に指定する場合は `--act4-exe` や `--fiumller-exe` 引数を利用可能。
