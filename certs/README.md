# Russian Trusted Root CA

The public root certificate of the Ministry of Digital Development (НУЦ Минцифры). GigaChat's servers
use it, so Lumelle trusts it **only for requests to GigaChat**; the system and other sites are not affected.
No private key is included.

- Source: the official Sber repository ai-forever/gpt2giga, which took it from
  https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer (see https://developers.sber.ru/docs/ru/gigachat/certificates).
- Subject/issuer: C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Root CA.
- Valid: 2022-03-01 … 2032-02-27.
- SHA-256 fingerprint: `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`
  (check: `openssl x509 -in russian_trusted_root_ca.pem -noout -fingerprint -sha256`).
