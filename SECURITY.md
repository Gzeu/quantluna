# Security Policy

## Versiuni suportate

| Version | Supported |
|---------|:---------:|
| 0.14.x  | ✅        |
| 0.13.x  | ✅        |
| < 0.13  | ❌        |

## Raportare vulnerabilitati

**Nu deschide un issue public pentru vulnerabilitati de securitate.**

Trimite un email la: `security@quantluna.dev` (sau contact direct prin GitHub)

Include in raport:
- Descrierea vulnerabilitatii
- Pasi de reproducere
- Impact potential
- Sugestii de remediere (optional)

Vei primi un raspuns in maxim **48 ore**.

## Practici de securitate

### Chei API
- Niciodata nu stoca cheile API in cod sau in git
- Foloseste intotdeauna `.env` (exclus din git prin `.gitignore`)
- Foloseste chei API cu **permisiuni minime** necesare (citire + tranzactionare, fara retrageri)
- Activeaza **IP whitelist** pe exchange pentru cheile live

### Paper trading
- Ruleaza intotdeauna cu `DRY_RUN=true` la inceput
- Testeaza pe testnet inainte de live trading
- Verifica logurile inainte de a trece pe live

### Productie
- Ruleaza containerele cu utilizator non-root (Dockerfile configurat)
- Monteaza `state/` si `data/` ca volume externe, nu in imagine
- Roteste cheile API regulat
- Monitorizeaza alertele NotifierBus (Slack/Telegram/Discord)

### Dependencies
- Dependentele sunt pinuite in `requirements.txt`
- Verifica periodic cu `pip audit` sau `safety check`
- CI ruleaza pe fiecare PR si merge in main
