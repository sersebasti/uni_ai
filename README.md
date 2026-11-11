# Uni AI

Prepara PDF universitari e offre una chat locale che risponde usando le pagine elaborate come fonti.

## Avvio

1. Inserisci i PDF in `materials/pdf/`.
2. Crea il file `.env` a partire da `.env.example` e inserisci la tua chiave OpenAI:

	```bash
	cp .env.example .env
	```

3. Avvia la chat e i servizi locali:

	```bash
	docker compose up --build web
	```

Apri `http://localhost:8000`. Dopo la build e l'avvio del server, l'interfaccia e subito disponibile: attesa dei servizi, preparazione del modello, preprocessing con OCR e indicizzazione vengono eseguiti in background. La pagina mostra lo stato di ogni fase, le pagine elaborate e i blocchi indicizzati con contatori e percentuali reali. Durante il download viene mostrato l'avanzamento del livello corrente, quando disponibile; le operazioni senza un totale noto hanno un indicatore indeterminato.

La chat si abilita solo quando tutte le fasi sono completate e la chiave OpenAI e configurata. Errori di elaborazione, servizi non disponibili e configurazione mancante sono mostrati nella pagina. Lo stato viene aggiornato automaticamente, anche ricaricando la pagina, tramite `/api/status`.

A ogni avvio del servizio `web`, i PDF vengono rielaborati e l'indice ricreato; il modello gia scaricato viene riutilizzato da Ollama. Dopo aver modificato i PDF, esegui `docker compose restart web`. Non avviare contemporaneamente i servizi manuali `preprocess` e `indexer`, ne piu istanze o worker di `web`: la preparazione automatica gestisce lo stesso manifest e indice.

`ollama` esegue localmente `bge-m3` per creare embedding; `chromadb` conserva l'indice vettoriale. Il preprocessing usa Tesseract in italiano e inglese sulle pagine con immagini o poco testo. Il testo OCR viene unito al testo nativo durante l'indicizzazione. I servizi manuali restano disponibili per manutenzione; per una verifica senza OCR usa `docker compose run --rm preprocess --skip-ocr`.

La chat cerca nell'indice vettoriale locale, invia a OpenAI solo le fonti più pertinenti e mostra le pagine usate per la risposta. Ogni fonte è un link che apre la relativa dispensa PDF in una nuova scheda.

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