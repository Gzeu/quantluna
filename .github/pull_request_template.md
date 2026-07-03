## Descriere

Scurta descriere a ce face acest PR.

Fixes #(issue)

## Tip de modificare

- [ ] Bug fix (schimbare non-breaking care rezolva un issue)
- [ ] Feature noua (schimbare non-breaking care adauga functionalitate)
- [ ] Breaking change (fix sau feature care ar putea afecta functionalitatea existenta)
- [ ] Refactor (fara schimbari functionale)
- [ ] Docs / Config

## Checklist

- [ ] Codul respecta stilul proiectului (`make lint` trece)
- [ ] Am adaugat/actualizat teste
- [ ] Toate testele trec (`make test`)
- [ ] Am actualizat documentatia daca e necesar
- [ ] `DRY_RUN=true` testat pentru orice modificari in `execution/`
- [ ] Nu am commit-at chei API sau date sensibile

## Teste rulate

```bash
make test
# sau
pytest tests/test_XYZ.py -v
```

## Screenshot / output (daca relevanta)
