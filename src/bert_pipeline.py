# -*- coding: utf-8 -*-
"""BERT_pipeline.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/128oFNIxYrCIl0pQWJlOfDe7Z356NclKD

### Setup
"""

# !pip install transformers datasets pytorch_lightning torchmetrics==0.6.0 tqdm sentencepiece googletrans==3.1.0a0;

import pandas as pd
import numpy as np
import matplotlib

import seaborn as sns
import matplotlib.pyplot as plt

import transformers
from transformers import CamembertModel, CamembertTokenizer
from datasets import load_dataset, load_dataset_builder

import torch
from torch.utils.data import Dataset, DataLoader
from torch.optim import AdamW
import pytorch_lightning as pl
from torchmetrics.functional import accuracy, f1, auroc
from pytorch_lightning.callbacks import ModelCheckpoint
from pytorch_lightning.loggers import TensorBoardLogger
from pytorch_lightning.callbacks.early_stopping import EarlyStopping

from sklearn import metrics
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

from tqdm import tqdm
from googletrans import Translator

import unicodedata
import re
import gc
import os

data_path = "drive/MyDrive/BERT/new-data"

DROP_NONE = True
UNDER_SAMPLE_OVERS = True
UNDER_SAMPLE_PLUS = True
WITH_TRAN_TO_EN = True

SUBMIT_TEST = False

"""### Config"""

# EN_BERT_MODEL_NAME = 'roberta-base'
# MULTI_BERT_MODEL_NAME = "bert-base-multilingual-uncased"

# ----------   ---------------   -------------  -----------
BERT_MODEL_NAME = "bert-base-german-cased"
ID = "new-aug-drop-over-tra-en"
MODEL = BERT_MODEL_NAME
# ----------   ---------------   -------------  -----------

LANG = "en"

# LANG, MAX = "fr", 600
# LANG, MAX = "ge", 600
# LANG, MAX = "it", 600
# LANG, MAX = "po", 600
# LANG, MAX = "ru", 600

BERT_MODEL_NAME, ID

try:
  # os.mkdir(f"{data_path}/{ID}/{LANG}/model")
  os.mkdir(f"{data_path}/{ID}/{LANG}/model/{MODEL}")
except:
  pass

aug_df = pd.read_csv(f"{data_path}/{ID}/{LANG}/{LANG}.csv")
len(aug_df)

LABEL_COLUMNS = aug_df.columns.tolist()[3:]
len(aug_df[~(aug_df[LABEL_COLUMNS]==0).all(axis=1)])

# print(LANG)
# aug_df[LABEL_COLUMNS].sum().sort_values().plot(kind="barh", title="Augmented English Training Dataset")
# plt.savefig('aug_en')

MAX = 1000

tokenizer = transformers.AutoTokenizer.from_pretrained(BERT_MODEL_NAME)
MAX_TOKEN_COUNT = 300

"""### Augment"""

def under(df_, max_):
  df_noco = df_[df_[LABEL_COLUMNS].apply(lambda x: sum(x), axis=1) == 1]
  df_co = df_[df_[LABEL_COLUMNS].apply(lambda x: sum(x), axis=1) > 1]
  
  cols_to_drop = [col for col in LABEL_COLUMNS if df_co[col].sum() > max_]
  print("dropping from", cols_to_drop)
  df_noco_dropped = df_noco[~(df_noco[cols_to_drop].eq(1).any(1))]

  df_[LABEL_COLUMNS].sum().sort_values().plot(kind="barh")
  plt.show()

  under_df = pd.concat([df_co, df_noco_dropped])
  under_df[LABEL_COLUMNS].sum().sort_values().plot(kind="barh")
  
  return under_df

def drop_top_2(df_):
  
  cols_drop = df_[LABEL_COLUMNS].sum().sort_values(ascending=False).index.tolist()[:6]
  cols_keep = [col for col in LABEL_COLUMNS if col not in cols_drop]
  print(cols_drop)
  dropped_df = df_[~((df_[cols_drop].eq(1).any(1)) & (df_[cols_keep].sum() == 0))]
  
  dropped_df[LABEL_COLUMNS].sum().sort_values().plot(kind="barh", title="Augmented English Training Dataset", xticks=[0, 500, 1000, 1500, 2000, 2500])
  plt.show()
  
  return dropped_df

"""### Pytorch"""

class TextDataset(Dataset):
  def __init__(
    self,
    data: pd.DataFrame,
    tokenizer,
    max_token_len: int = 128
  ):
    self.tokenizer = tokenizer
    self.data = data
    self.max_token_len = max_token_len
  
  def __len__(self):
    return len(self.data)
  
  def __getitem__(self, index: int):
    data_row = self.data.iloc[index]
    text = data_row.text
    labels = data_row[LABEL_COLUMNS]
    encoding = self.tokenizer.encode_plus(
      text,
      add_special_tokens=True,
      max_length=self.max_token_len,
      return_token_type_ids=False,
      padding="max_length",
      truncation=True,
      return_attention_mask=True,
      return_tensors='pt',
    )
    return dict(
      text=text,
      input_ids=encoding["input_ids"].flatten(),
      attention_mask=encoding["attention_mask"].flatten(),
      labels=torch.FloatTensor(labels)
    )

class TextDataModule(pl.LightningDataModule):
  def __init__(self, train_df, test_df, tokenizer, batch_size=8, max_token_len=512):
    super().__init__()
    self.batch_size = batch_size
    self.train_df = train_df
    self.test_df = test_df
    self.tokenizer = tokenizer
    self.max_token_len = max_token_len
  def setup(self, stage=None):
    self.train_dataset = TextDataset(
      self.train_df,
      self.tokenizer,
      self.max_token_len
    )
    self.test_dataset = TextDataset(
      self.test_df,
      self.tokenizer,
      self.max_token_len
    )
  def train_dataloader(self):
    return DataLoader(
      self.train_dataset,
      batch_size=self.batch_size,
      shuffle=True,
      num_workers=12
    )
  def val_dataloader(self):
    return DataLoader(
      self.test_dataset,
      batch_size=self.batch_size,
      num_workers=12
    )
  def test_dataloader(self):
    return DataLoader(
      self.test_dataset,
      batch_size=self.batch_size,
      num_workers=12
    )

class BERTTagger(pl.LightningModule):
  def __init__(self, n_classes: int, n_training_steps=None, n_warmup_steps=None):
    super().__init__()
    # config = transformers.AutoConfig.from_pretrained(BERT_MODEL_NAME, vocab_size=len(tokenizer.vocab))
    # self.bert = transformers.AutoModel.from_pretrained(BERT_MODEL_NAME, config=config, return_dict=True)
    self.bert = transformers.AutoModel.from_pretrained(BERT_MODEL_NAME, return_dict=True)
    self.classifier = torch.nn.Linear(self.bert.config.hidden_size, n_classes)
    self.n_training_steps = n_training_steps
    self.n_warmup_steps = n_warmup_steps
    self.criterion = torch.nn.BCELoss()

  def forward(self, input_ids, attention_mask, labels=None):
    output = self.bert(input_ids, attention_mask=attention_mask)
    output = self.classifier(output.pooler_output)
    output = torch.sigmoid(output)
    loss = 0
    if labels is not None:
        loss = self.criterion(output, labels)
    return loss, output

  def training_step(self, batch, batch_idx):
    input_ids = batch["input_ids"]
    attention_mask = batch["attention_mask"]
    labels = batch["labels"]
    loss, outputs = self(input_ids, attention_mask, labels)
    self.log("train_loss", loss, prog_bar=True, logger=True)
    return {"loss": loss, "predictions": outputs, "labels": labels}

  def validation_step(self, batch, batch_idx):
    input_ids = batch["input_ids"]
    attention_mask = batch["attention_mask"]
    labels = batch["labels"]
    loss, outputs = self(input_ids, attention_mask, labels)
    self.log("val_loss", loss, prog_bar=True, logger=True)
    return loss
  
  def test_step(self, batch, batch_idx):
    input_ids = batch["input_ids"]
    attention_mask = batch["attention_mask"]
    labels = batch["labels"]
    loss, outputs = self(input_ids, attention_mask, labels)
    self.log("test_loss", loss, prog_bar=True, logger=True)
    return loss
  
  def training_epoch_end(self, outputs):
    labels = []
    predictions = []
    for output in outputs:
      for out_labels in output["labels"].detach().cpu():
        labels.append(out_labels)
      for out_predictions in output["predictions"].detach().cpu():
        predictions.append(out_predictions)
    labels = torch.stack(labels).int()
    predictions = torch.stack(predictions)
    
    for i, name in enumerate(LABEL_COLUMNS):
      class_roc_auc = auroc(predictions[:, i], labels[:, i])
      self.logger.experiment.add_scalar(f"{name}_roc_auc/Train", class_roc_auc, self.current_epoch)
  
  def configure_optimizers(self):
    optimizer = AdamW(self.parameters(), lr=2e-5)
    scheduler = transformers.get_linear_schedule_with_warmup(
      optimizer,
      num_warmup_steps=self.n_warmup_steps,
      num_training_steps=self.n_training_steps
    )
    return dict(
      optimizer=optimizer,
      lr_scheduler=dict(
        scheduler=scheduler,
        interval='step'
      )
    )

"""### Train"""

def train_model(train_df__, valid_df):
  
  under_df = under(train_df__, MAX)
  under_plus_df = drop_top_2(under_df)

  print("FINAL LEN", len(under_plus_df))

  print("dropping nones")
  under_plus_df_drop_none = under_plus_df[~(under_plus_df[LABEL_COLUMNS]==0).all(axis=1)]
  print("FINAL LEN DROP NONE", len(under_plus_df_drop_none))
  
  assert(under_plus_df_drop_none["Whataboutism"].sum() == train_df__["Whataboutism"].sum())

  train_df = under_plus_df_drop_none
  BATCH_SIZE = 8

  steps_per_epoch=len(train_df) // BATCH_SIZE
  total_training_steps = steps_per_epoch * N_EPOCHS
  warmup_steps = total_training_steps // 5
  warmup_steps, total_training_steps
  

  model = BERTTagger(
    n_classes=len(LABEL_COLUMNS),
    n_warmup_steps=warmup_steps,
    n_training_steps=total_training_steps
  )

  data_module = TextDataModule(
    train_df,
    valid_df,
    tokenizer,
    batch_size=BATCH_SIZE,
    max_token_len=MAX_TOKEN_COUNT
  )

  checkpoint_callback = ModelCheckpoint(
    dirpath="checkpoints",
    filename="best-checkpoint",
    save_top_k=1,
    verbose=True,
    monitor="val_loss",
    mode="min"
    )

  logger = TensorBoardLogger("lightning_logs", name="persuasive-text")

  early_stopping_callback = EarlyStopping(monitor='val_loss', min_delta=0.00, patience=2)

  trainer = pl.Trainer(
    logger=logger,
    checkpoint_callback=checkpoint_callback,
    callbacks=[early_stopping_callback],
    max_epochs=N_EPOCHS,
    gpus=1,
    progress_bar_refresh_rate=30
  )
  
  trainer.fit(model, data_module)

  trained_model = BERTTagger.load_from_checkpoint(
    trainer.checkpoint_callback.best_model_path,
    n_classes=len(LABEL_COLUMNS)
  )
  trained_model.eval()
  trained_model.freeze()

  return trained_model

"""### Run and Predict"""

def run(run_num):

  device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

  model_run__path = f"{data_path}/{ID}/{LANG}/model/{MODEL}/{run_num}"

  try:
    os.mkdir(model_run__path)
  except:
    pass

  train_split_df, valid_split_df = train_test_split(aug_df, test_size=0.2)

  trained_model = train_model(train_split_df, valid_split_df)
  trained_model = trained_model.to(device)

  print(BERT_MODEL_NAME, ID, LANG, model_run__path)

  langs_to_pred = ["en", "fr", "ge", "it", "po", "ru"]

  for lang_to_pred in langs_to_pred:

    model_run_pred_path = f"{model_run__path}/{lang_to_pred}"
    gold_path = f"{model_run_pred_path}/{LANG}-gold-{lang_to_pred}.txt"
    pred_path = f"{model_run_pred_path}/{LANG}-pred-{lang_to_pred}.txt"

    try:
      os.mkdir(model_run_pred_path)
    except:
      pass

    test_df_gold = pd.read_csv(f"{data_path}/dev-sets-en/{lang_to_pred}-dev-sets-en.csv")

    for label in LABEL_COLUMNS:
      test_df_gold[label] = np.where(test_df_gold[label] == 1, label, "")
    test_df_gold["tags"] = test_df_gold[LABEL_COLUMNS].apply(lambda row: ','.join(row[row != ""].astype(str)), axis=1)

    test_df_gold = test_df_gold.drop(columns=LABEL_COLUMNS + ["text"])

    test_df_gold.to_csv(gold_path, sep ='\t', header=False, index=False)

    test_df = pd.read_csv(f"{data_path}/dev-sets-en/{lang_to_pred}-dev-sets-en.csv")

    for col in LABEL_COLUMNS:
      test_df[col].values[:] = 0
    
    
    dataset = TextDataset(
      test_df.copy(),
      tokenizer,
      max_token_len=MAX_TOKEN_COUNT
    )

    predictions = []
    labels = []


    for item in tqdm(dataset):
      _, prediction = trained_model(
        item["input_ids"].unsqueeze(dim=0).to(device),
        item["attention_mask"].unsqueeze(dim=0).to(device)
      )
      
      predictions.append(prediction.flatten())
    

    predictions = torch.stack(predictions).detach().cpu()

    THRESHOLD = 0.5
    upper, lower = 1, 0
    predictions = np.where(predictions > THRESHOLD, upper, lower)

    final_preds_df = pd.DataFrame(predictions, columns=LABEL_COLUMNS)    
    final_preds_df = pd.concat([test_df[['article', 'paragraph']].reset_index(drop=True), final_preds_df], axis=1)



    for label in LABEL_COLUMNS:
      final_preds_df[label] = np.where(final_preds_df[label] == 1, label, "")
    final_preds_df["tags"] = final_preds_df[LABEL_COLUMNS].apply(lambda row: ','.join(row[row != ""].astype(str)), axis=1)

    final_preds_df = final_preds_df.drop(columns=LABEL_COLUMNS)

    final_preds_df.to_csv(pred_path, sep ='\t', header=False, index=False)

"""### Results"""

scorer = "./drive/MyDrive/BERT/data/scorer-subtask-3.py"
labels = "./drive/MyDrive/BERT/data/techniques_subtask3.txt"

def get_gold(lang_____pred, run):
  path = f"{data_path}/{ID}/{LANG}/model/{MODEL}/{run}/{lang_____pred}"
  return f"{path}/{LANG}-gold-{lang_____pred}.txt"

def get_pred(lang_____pred, run):
  path = f"{data_path}/{ID}/{LANG}/model/{MODEL}/{run}/{lang_____pred}"
  return f"{path}/{LANG}-pred-{lang_____pred}.txt"

N_EPOCHS = 10
run(1)

RUN_NUM = 1

# !echo $RUN_NUM
# !echo $BERT_MODEL_NAME

# PRED_LANG = "en"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "fr"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ge"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "it"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "po"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ru"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}

# run(2)

# RUN_NUM = 2

# !echo $RUN_NUM
# !echo $BERT_MODEL_NAME

# PRED_LANG = "en"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "fr"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ge"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "it"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "po"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ru"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}

# run(3)

# RUN_NUM = 3

# !echo $RUN_NUM
# !echo $BERT_MODEL_NAME

# PRED_LANG = "en"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "fr"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ge"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "it"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "po"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ru"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}

# run(4)

# RUN_NUM = 4

# !echo $RUN_NUM
# !echo $BERT_MODEL_NAME

# PRED_LANG = "en"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "fr"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ge"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "it"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "po"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ru"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}

# run(5)

# RUN_NUM = 5

# !echo $RUN_NUM
# !echo $BERT_MODEL_NAME

# PRED_LANG = "en"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "fr"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ge"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "it"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "po"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}



# PRED_LANG = "ru"
# GOLD = get_gold(PRED_LANG, RUN_NUM)
# PRED = get_pred(PRED_LANG, RUN_NUM)

# !echo $LANG "pred" $PRED_LANG
# !python3 {scorer} -g {GOLD} -p {PRED} -f {labels}

