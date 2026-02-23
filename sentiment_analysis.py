import sys
import os
import gzip
import pickle
from sqlalchemy import create_engine
from api_keys import database, huggingface
from typing import Dict
import pandas as pd
from datetime import datetime
from transformers import AutoTokenizer
from huggingface_hub import InferenceClient
import time

#! Must solve for strong coupling to pickled symbol data.
class SentimentAnalysis:
    def __init__(self, symbols: Dict[str, pd.DataFrame]= None):
        if symbols is None:
            sys.path.insert(0, r"C:\Users\jdejo\Market_Data_Processing")
            path = r"E:\Market Research\Dataset\daily_after_close_study\symbols.pkl.gz"
            with gzip.open(path, "rb") as f:          # rb + pickle.load for reading
                symbols = pickle.load(f)
        self.symbols = symbols
        url = f"mysql+pymysql://root:{database}@127.0.0.1:3306/news"
        self.engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        
        self.act_vol_bullish_sentiment_frames = {}
        self.act_vol_bearish_sentiment_frames = {}
    
    def act_vol_bullish_sentiment(self, 
                                  date: str = None, 
                                  token_limit_override: bool = False,
                                  concat_frames: bool = True):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        act_vol_bullish = []
        for sym in self.symbols:
            if (
                (self.symbols[sym].df.iloc[-1].RVol > 2) and 
                (self.symbols[sym].df.iloc[-1].ATRs_Traded > 1.5) and 
                (self.symbols[sym].df.Close.iloc[-1] > self.symbols[sym].df.Open.iloc[-1])
                ):
                act_vol_bullish.append(sym)

        act_vol_bullish_news = {}
        for sym in act_vol_bullish:
            query = f"SELECT * FROM {sym} WHERE date >= '{date} 00:00:00'"
            df = pd.read_sql_query(query, con=self.engine)
            if not df.empty:
                act_vol_bullish_news[sym] = df

        tok = AutoTokenizer.from_pretrained("mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis", trust_remote_code=True)
        tokens = 0
        for sym in act_vol_bullish_news:
            for i in range(len(act_vol_bullish_news[sym])):
                tokens += len(tok.encode(act_vol_bullish_news[sym].iloc[i].Title))
        if (tokens > 50_000) and (token_limit_override is False):
            raise ValueError(f"{tokens} exceeds token limit (50,000) and token_limit_override is False. Set token_limit_override to True to override the token limit.")

        client = InferenceClient(
            provider="hf-inference",
            api_key=huggingface,
        )
        
        
        def _inference(df):
            result = client.text_classification(
                df.Title,
                model="mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis",
            )
            if result:
                return result
            else:
                return []

        for sym in act_vol_bullish_news:
            act_vol_bullish_news[sym]['sentiment'] = act_vol_bullish_news[sym].apply(lambda x: _inference(x), axis=1)
            time.sleep(.1)
            
        self.act_vol_bullish_sentiment_frames = act_vol_bullish_news
        if concat_frames:
            act_vol_bullish_news = pd.concat(act_vol_bullish_news.values())
            # act_vol_bullish_news = act_vol_bullish_news.reset_index(drop=True)
            # act_vol_bullish_news = act_vol_bullish_news.sort_values(by='date', ascending=True)
            # act_vol_bullish_news = act_vol_bullish_news.reset_index(drop=True)
            # act_vol_bullish_news is your DataFrame (must have a 'Ticker' column)
            # pull first model output (label/score) out of the list
            act_vol_bullish_news["sentiment_label0"] = act_vol_bullish_news["sentiment"].apply(lambda s: s[0]["label"] if s else None)
            act_vol_bullish_news["sentiment_score0"] = act_vol_bullish_news["sentiment"].apply(lambda s: s[0]["score"] if s else None)
            act_vol_bullish_news["sentiment_label1"] = act_vol_bullish_news["sentiment"].apply(lambda s: s[1]["label"] if s else None)
            act_vol_bullish_news["sentiment_score1"] = act_vol_bullish_news["sentiment"].apply(lambda s: s[1]["score"] if s else None)
            act_vol_bullish_news["sentiment_label2"] = act_vol_bullish_news["sentiment"].apply(lambda s: s[2]["label"] if s else None)
            act_vol_bullish_news["sentiment_score2"] = act_vol_bullish_news["sentiment"].apply(lambda s: s[2]["score"] if s else None)
            
            # sort within ticker by that first label (alphabetical)
            act_vol_bullish_news_sorted = act_vol_bullish_news.sort_values(["Ticker", "sentiment_label0"], ascending=[True, True])
            # now group if you want (example: inspect groups)
            act_vol_bullish_news = act_vol_bullish_news_sorted.groupby("Ticker", sort=False)
            
        return act_vol_bullish_news
    
    def act_vol_bearish_sentiment(self, 
                                  date: str = None, 
                                  token_limit_override: bool = False,
                                  concat_frames: bool = False):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        act_vol_bearish = []
        for sym in self.symbols:
            if (
                (self.symbols[sym].df.iloc[-1].RVol > 2) and 
                (self.symbols[sym].df.iloc[-1].ATRs_Traded > 1.5) and 
                (self.symbols[sym].df.Close.iloc[-1] < self.symbols[sym].df.Open.iloc[-1])
                ):
                act_vol_bearish.append(sym)

        act_vol_bearish_news = {}
        for sym in act_vol_bearish:
            query = f"SELECT * FROM {sym} WHERE date >= '{date} 00:00:00'"
            df = pd.read_sql_query(query, con=self.engine)
            if not df.empty:
                act_vol_bearish_news[sym] = df

        tok = AutoTokenizer.from_pretrained("mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis", trust_remote_code=True)
        tokens = 0
        for sym in act_vol_bearish_news:
            for i in range(len(act_vol_bearish_news[sym])):
                tokens += len(tok.encode(act_vol_bearish_news[sym].iloc[i].Title))
        if (tokens > 50_000) and (token_limit_override is False):
            raise ValueError(f"{tokens} exceeds token limit (50,000) and token_limit_override is False. Set token_limit_override to True to override the token limit.")

        client = InferenceClient(
            provider="hf-inference",
            api_key=huggingface,
        )
        
        
        def _inference(df):
            result = client.text_classification(
                df.Title,
                model="mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis",
            )
            if result:
                return result
            else:
                return []

        for sym in act_vol_bearish_news:
            act_vol_bearish_news[sym]['sentiment'] = act_vol_bearish_news[sym].apply(lambda x: _inference(x), axis=1)
        
        self.act_vol_bearish_sentiment_frames = act_vol_bearish_news
        if concat_frames:
            act_vol_bearish_news = pd.concat(act_vol_bearish_news.values())
            # act_vol_bearish_news = act_vol_bearish_news.reset_index(drop=True)
            # act_vol_bearish_news = act_vol_bearish_news.sort_values(by='date', ascending=True)
            # act_vol_bearish_news = act_vol_bearish_news.reset_index(drop=True)
            act_vol_bearish_news["sentiment_label0"] = act_vol_bearish_news["sentiment"].apply(lambda s: s[0]["label"] if s else None)
            act_vol_bearish_news["sentiment_score0"] = act_vol_bearish_news["sentiment"].apply(lambda s: s[0]["score"] if s else None)
            act_vol_bearish_news["sentiment_label1"] = act_vol_bearish_news["sentiment"].apply(lambda s: s[1]["label"] if s else None)
            act_vol_bearish_news["sentiment_score1"] = act_vol_bearish_news["sentiment"].apply(lambda s: s[1]["score"] if s else None)
            act_vol_bearish_news["sentiment_label2"] = act_vol_bearish_news["sentiment"].apply(lambda s: s[2]["label"] if s else None)
            act_vol_bearish_news["sentiment_score2"] = act_vol_bearish_news["sentiment"].apply(lambda s: s[2]["score"] if s else None)
            
            # sort within ticker by that first label (alphabetical)
            act_vol_bearish_news_sorted = act_vol_bearish_news.sort_values(["Ticker", "sentiment_label0"], ascending=[True, True])
            # now group if you want (example: inspect groups)
            act_vol_bearish_news = act_vol_bearish_news_sorted.groupby("Ticker", sort=False)
        
        return act_vol_bearish_news
