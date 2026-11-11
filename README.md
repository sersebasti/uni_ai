# Uni AI

Prepara PDF universitari e offre una chat locale che risponde usando le pagine elaborate come fonti.

## Avvio

1. Inserisci le dispense teoriche in `materials/pdf/teoria/` e i manuali di Solid Edge in `materials/pdf/pratica/`. Le cartelle possono essere create prima di copiare i file:

	```bash
	mkdir -p materials/pdf/teoria materials/pdf/pratica
	```

	I PDF gia presenti direttamente in `materials/pdf/` continuano a essere considerati teoria: non serve spostarli e la loro cache rimane valida. Non usare lo stesso nome per due PDF all'interno della sezione teorica, anche tra la cartella principale e `teoria/`. Lo stesso nome puo invece essere usato in sezioni diverse.
2. Crea il file `.env` a partire da `.env.example` e inserisci la tua chiave OpenAI:

	```bash
	cp .env.example .env
	```

3. Avvia la chat e i servizi locali:

	```bash
	docker compose up --build web
	```

Apri `http://localhost:8000`. Dopo la build e l'avvio del server, l'interfaccia e subito disponibile: attesa dei servizi, preparazione del modello, preprocessing con OCR e indicizzazione vengono eseguiti in background. La pagina mostra lo stato di ogni fase, le pagine elaborate e i blocchi indicizzati con contatori e percentuali reali. Durante il download viene mostrato l'avanzamento del livello corrente, quando disponibile; le operazioni senza un totale noto hanno un indicatore indeterminato.

Ogni sezione si abilita quando le proprie fasi sono completate e la chiave OpenAI e configurata. Errori di elaborazione, servizi non disponibili e configurazione mancante sono mostrati nella pagina. Lo stato viene aggiornato automaticamente, anche ricaricando la pagina, tramite `/api/status?section=theory` oppure `/api/status?section=practice`; senza parametro viene restituito lo stato teorico.

## Teoria e pratica Solid Edge

I pulsanti **Teoria** e **Pratica - Solid Edge** selezionano conversazioni e fonti distinte. Le conversazioni, le bozze e gli allegati restano separati durante il cambio sezione; come prima, la cronologia della chat non e persistita dopo il ricaricamento della pagina.

La teoria mantiene le spiegazioni e la panoramica del corso gia disponibili. La pratica risponde esclusivamente dai manuali pratici e, per le domande operative, richiede al modello: ambiente e prerequisiti, comandi documentati, procedura numerata e controlli sul risultato. Puoi indicare versione di Solid Edge e lingua dei comandi; sono contesto della domanda, non certificazioni della compatibilita dei manuali. Includi versione e lingua anche nei nomi dei manuali per renderle identificabili nelle fonti. Quando la procedura o la compatibilita non sono documentate, l'assistente deve segnalarlo senza inventare menu o comandi. Non controlla direttamente il CAD.

Una sezione senza PDF mostra dove inserirli e disabilita solo la propria chat. Elaborazioni lente o errori dei manuali pratici non bloccano una teoria gia pronta, e viceversa. Dopo aver copiato nuovi PDF, riavvia `web`: il sistema controlla entrambe le sezioni, ma rielabora solo quella con dati modificati.

Le fonti aprono il PDF della sezione corretta e la pagina citata. Le API chat accettano `section` (`theory` o `practice`, default `theory`), `cad_version` e `cad_language`, sia come JSON sia con allegati multipart. Anche `/api/illustrations` accetta `section`; `/api/documents/{filename}?section=practice` distingue manuali e dispense con lo stesso nome.

## Cache di elaborazione

A ogni avvio del servizio `web`, il sistema controlla le firme persistenti del lavoro completato. Se il contenuto dei PDF e le impostazioni di elaborazione sono invariati, riutilizza il manifest senza ripetere OCR e rendering. L'indice viene riutilizzato solo se corrispondono anche il testo del manifest, i parametri di chunking, il nome e il digest del modello embedding e il numero di blocchi salvati. L'interfaccia indica esplicitamente le fasi riutilizzate. Cambiare interfaccia, modello chat, chiave OpenAI o modello immagini non invalida questi risultati.

Dopo aver aggiunto, modificato o rimosso PDF, esegui `docker compose restart web`: il preprocessing rielabora l'insieme dei PDF della sola sezione modificata e il relativo indice viene ricreato se il testo e cambiato. Se manca solo un indice, viene ricostruito senza ripetere il preprocessing. Il modello gia scaricato viene riutilizzato da Ollama. Non avviare contemporaneamente i servizi manuali `preprocess` e `indexer`, ne piu istanze o worker di `web`: la preparazione automatica gestisce gli stessi manifest e indici.

Le firme vengono salvate solo dopo il completamento. La teoria conserva `data/processed/preprocess-state.json`, il manifest esistente e la collezione ChromaDB `university_notes`. La pratica usa `data/processed/pratica/` e la collezione `solid_edge_manuals`. Risultati parziali, firme mancanti o non valide richiedono una nuova elaborazione della sola sezione interessata. Al primo avvio dopo l'aggiornamento da una versione senza firme e necessaria una preparazione completa, una sola volta; il passaggio alle due sezioni conserva invece le firme teoriche gia valide. Conserva sia `data/processed/` sia il volume ChromaDB tra i riavvii. Le versioni di elaborazione nel codice devono essere incrementate quando cambia la logica di estrazione/OCR o di indicizzazione.

`ollama` esegue localmente `bge-m3` per creare embedding; `chromadb` conserva l'indice vettoriale. Il preprocessing usa Tesseract in italiano e inglese sulle pagine con immagini o poco testo, con rilevamento automatico dell'orientamento per le scansioni ruotate. Il testo OCR viene unito al testo nativo durante l'indicizzazione. Gli indici prodotti con versioni precedenti devono essere rigenerati dal preprocessing per correggere il testo gia estratto. I servizi manuali restano disponibili per manutenzione; per una verifica senza OCR usa `docker compose run --rm preprocess --skip-ocr`.

Per elaborare manualmente solo la pratica, a servizio web fermo:

```bash
docker compose run --rm preprocess --input-dir materials/pdf/pratica --output-dir data/processed/pratica
docker compose run --rm indexer --section practice
```

Il servizio manuale `indexer` senza parametri continua a indicizzare la teoria.

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