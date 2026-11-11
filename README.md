# Uni AI

Prepara PDF universitari e offre una chat locale che risponde usando le pagine elaborate come fonti.

## Avvio

1. Inserisci i PDF in `materials/pdf/`.
2. Elabora le dispense:

	```bash
	docker compose run --rm preprocess
	```

3. Avvia la ricerca semantica locale e scarica una sola volta il modello di embedding:

	```bash
	docker compose up -d ollama chromadb
	docker compose run --rm ollama-pull
	docker compose run --rm indexer
	```

	`ollama` esegue localmente `bge-m3` per creare embedding. `chromadb` conserva l'indice vettoriale. Ripeti il comando `indexer` dopo aver aggiunto o modificato PDF.

	Il preprocessing usa Tesseract in italiano e inglese sulle pagine con immagini o poco testo. Il testo OCR viene unito al testo nativo durante l'indicizzazione. Per una verifica piu veloce senza OCR usa `docker compose run --rm preprocess --skip-ocr`.

4. Crea il file `.env` a partire da `.env.example` e inserisci la tua chiave OpenAI:

	```bash
	cp .env.example .env
	```

5. Avvia la chat:

	```bash
	docker compose up --build web
	```

Apri `http://localhost:8000`. La chat cerca nell'indice vettoriale locale, invia a OpenAI solo le fonti più pertinenti e mostra le pagine usate per la risposta. Ogni fonte è un link che apre la relativa dispensa PDF in una nuova scheda.

Puoi allegare screenshot o immagini PNG, JPEG e WebP fino a 10 MB. L'immagine viene inviata a OpenAI insieme alla domanda e alle fonti selezionate. Per ogni risposta puoi inoltre scegliere di generare un'illustrazione esplicativa: il risultato viene salvato localmente in `data/generated/`.

# Uni AI

Preprocessing locale di slide PDF per preparare domande di disegno tecnico al calcolatore.

## Python locale

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src\preprocess_pdfs.py
```

## Docker

Installa e avvia Docker Desktop, poi esegui:

```powershell
docker compose run --rm preprocess
```

Il container legge i PDF da `materials/pdf` in sola lettura e salva manifest e render in `data/processed` sul PC. La chiave API non viene copiata nel container.