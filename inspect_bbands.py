import yfinance as yf
import pandas as pd
import pandas_ta as ta

u='SOXX'
df=yf.download(u,start='2024-01-01',end='2024-03-01',progress=False)
print('origin cols',df.columns)
if isinstance(df.columns,pd.MultiIndex):
    df.columns=df.columns.get_level_values(0)
df.columns=[c.lower() for c in df.columns]

df.ta.bbands(length=20,std=2,append=True)
print('after bb cols',df.columns.tolist())
