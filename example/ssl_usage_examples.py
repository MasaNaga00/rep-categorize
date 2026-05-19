"""
dify_client SSL対応の使い方サンプル

シナリオ:
    社内CA署名のセルフホスティングDifyに接続する。
    通常のCA(certifi)では検証失敗するため、社内CAバンドルを指定する必要がある。
"""

# =============================================================================
# パターン1: 環境変数で指定(推奨、Notebook向け)
# =============================================================================
# .env ファイルに以下を記載:
#   DIFY_BASE_URL=https://dify.internal.example.com
#   DIFY_API_KEY=app-xxxxx
#   DIFY_CA_BUNDLE=certs/internal-ca.pem
#
# 証明書は certs/ ディレクトリに配置(.gitignore済)

from dotenv import load_dotenv
from dify_client import DifyClient

load_dotenv()  # .env を読み込む
client = DifyClient()  # 全て環境変数から取得
# → certs/internal-ca.pem で検証


# =============================================================================
# パターン2: 引数で明示的に指定
# =============================================================================

from pathlib import Path
from dify_client import DifyClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
client = DifyClient(
    base_url="https://dify.internal.example.com",
    api_key="app-xxxxx",
    ca_bundle=PROJECT_ROOT / "certs" / "internal-ca.pem",
)


# =============================================================================
# パターン3: 引数 > 環境変数の優先順位
# =============================================================================
# 環境変数で certs/ca1.pem を設定済みでも、引数を渡すとそちらが使われる

client = DifyClient(ca_bundle="certs/ca2.pem")  # ca2.pem が使われる


# =============================================================================
# パターン4: 証明書なしで接続(クラウドDifyや、社内CAが既にOSにインストール済の環境)
# =============================================================================
# DIFY_CA_BUNDLE を空または未設定にする
# → certifi のCAバンドルで通常検証

client = DifyClient(base_url="...", api_key="...")  # ca_bundle未指定


# =============================================================================
# よくあるエラーと対処
# =============================================================================
# requests.exceptions.SSLError:
#     [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed
# → DIFY_CA_BUNDLE を設定する
#
# DifyClientError: CA bundle ファイルが見つかりません: certs/xxx.pem
# → パスが正しいか確認、ファイルが存在するか確認
#
# DifyClientError: CA bundle のパスがファイルではありません: /tmp
# → ディレクトリではなく証明書ファイルそのものを指定する
