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

A ogni avvio del servizio `web`, il sistema controlla le firme persistenti del lavoro completato. Se il contenuto dei PDF e le impostazioni di elaborazione sono invariati, riutilizza il manifest senza ripetere OCR e rendering. L'indice viene riutilizzato solo se corrispondono anche il testo del manifest, i parametri di chunking, il nome e il digest del modello embedding e il numero di blocchi salvati. L'interfaccia indica esplicitamente le fasi riutilizzate. Cambiare interfaccia, modello chat, chiave OpenAI o modello immagini non invalida questi risultati.

Dopo aver aggiunto, modificato o rimosso PDF, esegui `docker compose restart web`: in questo caso il preprocessing rielabora l'insieme dei PDF e l'indice viene ricreato se il testo e cambiato. Se manca solo l'indice, viene ricostruito senza ripetere il preprocessing. Il modello gia scaricato viene riutilizzato da Ollama. Non avviare contemporaneamente i servizi manuali `preprocess` e `indexer`, ne piu istanze o worker di `web`: la preparazione automatica gestisce lo stesso manifest e indice.

Le firme vengono salvate solo dopo il completamento: `data/processed/preprocess-state.json` per il preprocessing e i metadati della collezione ChromaDB per l'indice. Risultati parziali, firme mancanti o non valide richiedono una nuova elaborazione. Al primo avvio dopo l'aggiornamento da una versione senza firme e quindi necessaria una preparazione completa, una sola volta. Conserva sia `data/processed/` sia il volume ChromaDB tra i riavvii. Le versioni di elaborazione nel codice devono essere incrementate quando cambia la logica di estrazione/OCR o di indicizzazione.

`ollama` esegue localmente `bge-m3` per creare embedding; `chromadb` conserva l'indice vettoriale. Il preprocessing usa Tesseract in italiano e inglese sulle pagine con immagini o poco testo, con rilevamento automatico dell'orientamento per le scansioni ruotate. Il testo OCR viene unito al testo nativo durante l'indicizzazione. Gli indici prodotti con versioni precedenti devono essere rigenerati dal preprocessing per correggere il testo gia estratto. I servizi manuali restano disponibili per manutenzione; per una verifica senza OCR usa `docker compose run --rm preprocess --skip-ocr`.

La chat cerca nell'indice vettoriale locale, invia a OpenAI solo le fonti più pertinenti e mostra le pagine usate per la risposta. Ogni fonte è un link che apre la relativa dispensa PDF in una nuova scheda.

Per domande generali sul corso (ad esempio "Di cosa parla questo corso universitario?" o "Quali argomenti tratta il corso?"), la chat usa il primo blocco della prima pagina di ogni dispensa, fino a 20 dispense, anziche affidarsi ai soli sei risultati della ricerca semantica. Questa panoramica si basa sulle introduzioni disponibili, non garantisce di coprire tutte le pagine del corso. Le domande specifiche continuano a usare la ricerca semantica. Le citazioni della stessa pagina vengono mostrate una sola volta.

Puoi allegare screenshot o immagini PNG, JPEG e WebP fino a 10 MB. L'immagine viene inviata a OpenAI insieme alla domanda e alle fonti selezionate. Per ogni risposta puoi inoltre scegliere di generare un'illustrazione esplicativa: il risultato viene salvato localmente in `data/generated/`.

La generazione delle illustrazioni usa `OPENAI_IMAGE_MODEL` (default `gpt-image-1.5`), richiede accesso alle API immagini e puo comportare costi aggiuntivi. Se nel tuo `.env` e ancora impostato `gpt-image-1`, aggiorna il valore a un modello immagini disponibile per il tuo progetto. Se la generazione fallisce, il motivo viene mostrato sotto il pulsante: controlla credito e limiti API, permessi del progetto ed eventuale verifica dell'organizzazione. Il fatto che la chat funzioni non garantisce l'accesso alla generazione immagini. I log del servizio web riportano stato HTTP, codice errore e identificativo della richiesta OpenAI, senza la chiave API.

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