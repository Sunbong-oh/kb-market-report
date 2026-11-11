"""회사 PC 처럼 보안 장비가 HTTPS 를 가로채 회사 인증서로 바꿔 붙이는 곳(CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain)에서,
윈도 인증서 저장소(IT 가 설치한 회사 인증서 포함)를 파이썬도 믿게 한다. 검증을 끄는 것이 아니라, 그 PC 의 브라우저가 이미 믿는 인증서를 함께 쓰는 것이다.
윈도가 아니거나 이미 SSL_CERT_FILE 이 있으면 아무것도 하지 않는다. 실행 파일 맨 위에서 `import tls_trust  # noqa: F401` 한 줄로 쓴다.
환경변수로 정하므로 이 프로세스가 띄우는 다른 파이썬(서버·실적 감시)도 이어받는다."""
import os
import sys


def _setup() -> None:
    if sys.platform != "win32" or os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import ssl
        import tempfile

        import certifi

        pems = []
        for store in ("ROOT", "CA"):
            for cert, enc, trust in ssl.enum_certificates(store):
                if enc == "x509_asn" and (trust is True or "1.3.6.1.5.5.7.3.1" in trust):  # 서버 인증용만
                    pems.append(ssl.DER_cert_to_PEM_cert(cert))
        if not pems:
            return
        path = os.path.join(tempfile.gettempdir(), "kb_report_ca_bundle.pem")
        with open(certifi.where(), encoding="ascii") as f:
            base = f.read()
        with open(path, "w", encoding="ascii") as f:
            f.write(base + "\n" + "\n".join(pems) + "\n")
        os.environ["SSL_CERT_FILE"] = path
        os.environ["REQUESTS_CA_BUNDLE"] = path
    except Exception as e:  # 실패해도 예전처럼 동작
        print("tls_trust: 윈도 인증서 저장소를 못 읽음:", type(e).__name__, e)


_setup()
