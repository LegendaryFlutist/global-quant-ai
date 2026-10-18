"""A causal two-layer PyTorch GRU classifier; normalization fits training only."""
from pathlib import Path
import json
import numpy as np


class TorchGRUClassifier:
    def __init__(self,sequence_length=24,hidden_size=32,num_layers=2,epochs=20,batch_size=128,seed=42,device='auto'):
        self.sequence_length=sequence_length;self.hidden_size=hidden_size;self.num_layers=num_layers
        self.epochs=epochs;self.batch_size=batch_size;self.seed=seed;self.requested_device=device
        self.classes_=np.array([-1,0,1]);self.context_=None

    def _network(self,features):
        import torch
        class Network(torch.nn.Module):
            def __init__(net):
                super().__init__()
                net.gru=torch.nn.GRU(features,self.hidden_size,self.num_layers,batch_first=True,dropout=.15)
                net.head=torch.nn.Sequential(torch.nn.LayerNorm(self.hidden_size),torch.nn.Linear(self.hidden_size,16),torch.nn.GELU(),torch.nn.Dropout(.1),torch.nn.Linear(16,3))
            def forward(net,x):
                output,_=net.gru(x)
                return net.head(output[:,-1])
        return Network()

    def fit(self,X,y):
        import torch
        torch.manual_seed(self.seed);torch.set_num_threads(1)
        # Keep research probabilities in IEEE FP32 across CPU/GPU checkpoints.
        torch.backends.cuda.matmul.fp32_precision='ieee'
        torch.backends.cudnn.rnn.fp32_precision='ieee'
        if torch.cuda.is_available():torch.cuda.manual_seed_all(self.seed)
        self.device_='cuda' if self.requested_device=='auto' and torch.cuda.is_available() else 'cpu' if self.requested_device=='auto' else self.requested_device
        self.feature_names_=list(X.columns)
        values=np.asarray(X,dtype=np.float32);indices=np.asarray(X.index,dtype=np.int64)
        if not np.isfinite(values).all():raise ValueError('GRU training features must be finite')
        self.mean_=values.mean(axis=0);self.scale_=values.std(axis=0);self.scale_[self.scale_<1e-6]=1.
        scaled=np.clip((values-self.mean_)/self.scale_,-12,12)
        windows=[];targets=[];ends=[]
        labels=np.asarray(y,dtype=int)
        for i in range(self.sequence_length-1,len(values)):
            if np.all(np.diff(indices[i-self.sequence_length+1:i+1])==1):
                windows.append(scaled[i-self.sequence_length+1:i+1]);targets.append(labels[i]+1);ends.append(int(indices[i]))
        if len(windows)<16:raise ValueError('Not enough contiguous training sequences for GRU')
        x=torch.tensor(np.stack(windows),device=self.device_);target=torch.tensor(targets,device=self.device_,dtype=torch.long)
        counts=np.bincount(targets,minlength=3)
        weights=len(targets)/(3*np.maximum(counts,1))
        loss_fn=torch.nn.CrossEntropyLoss(weight=torch.tensor(weights,dtype=torch.float32,device=self.device_))
        self.network_=self._network(values.shape[1]).to(self.device_)
        optimizer=torch.optim.AdamW(self.network_.parameters(),lr=.001,weight_decay=.01)
        self.loss_history_=[]
        for epoch in range(self.epochs):
            self.network_.train();total=0.
            # Chronological batches; no validation or holdout drives stopping.
            for start in range(0,len(x),self.batch_size):
                xb=x[start:start+self.batch_size];yb=target[start:start+self.batch_size]
                optimizer.zero_grad(set_to_none=True);loss=loss_fn(self.network_(xb),yb)
                if not torch.isfinite(loss):raise ValueError('Nonfinite GRU loss')
                loss.backward();torch.nn.utils.clip_grad_norm_(self.network_.parameters(),1.);optimizer.step()
                total+=float(loss.detach())*len(xb)
            self.loss_history_.append(total/len(x))
        self.training_audit_=dict(device=self.device_,torch=torch.__version__,sequence_length=self.sequence_length,hidden_size=self.hidden_size,
            num_layers=self.num_layers,epochs=self.epochs,sequences=len(windows),class_counts=counts.tolist(),
            first_window_end=ends[0],last_window_end=ends[-1],loss_history=self.loss_history_,
            parameters=sum(p.numel() for p in self.network_.parameters()),normalization='training-only',future_inputs=False,
            gpu_name=torch.cuda.get_device_name(0) if self.device_.startswith('cuda') else None)
        self.set_context(X.tail(self.sequence_length-1))
        return self

    def set_context(self,X):
        # Context may include past, unlabeled candles purged from training. Only
        # input features are accepted here, never their future labels.
        self.context_=X[self.feature_names_].tail(self.sequence_length-1).copy()

    def predict_proba(self,X):
        import torch
        import pandas as pd
        X=X[self.feature_names_]
        context=self.context_ if self.context_ is not None else X.iloc[:0]
        context=context[context.index<X.index[0]] if len(X) else context
        combined=pd.concat([context,X]);values=np.asarray(combined,dtype=np.float32)
        scaled=np.clip((values-self.mean_)/self.scale_,-12,12);indices=np.asarray(combined.index,dtype=np.int64)
        result=np.tile([0.,1.,0.],(len(X),1));windows=[];positions=[]
        for j in range(len(X)):
            i=len(context)+j;left=i-self.sequence_length+1
            if left>=0 and np.all(np.diff(indices[left:i+1])==1) and np.isfinite(scaled[left:i+1]).all():
                windows.append(scaled[left:i+1]);positions.append(j)
        if windows:
            self.network_.eval()
            with torch.inference_mode():
                for left in range(0,len(windows),self.batch_size):
                    x=torch.tensor(np.stack(windows[left:left+self.batch_size]),device=self.device_)
                    prediction=torch.softmax(self.network_(x),dim=1).cpu().numpy()
                    result[np.array(positions[left:left+self.batch_size])]=prediction
        return result

    def export(self,path):
        import torch
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        torch.save(dict(architecture='two_layer_gru',state_dict={k:v.detach().cpu() for k,v in self.network_.state_dict().items()},
            feature_names=self.feature_names_,mean=self.mean_.tolist(),scale=self.scale_.tolist(),classes=self.classes_.tolist(),
            training=self.training_audit_),path)
        path.with_suffix('.json').write_text(json.dumps(self.training_audit_,ensure_ascii=False,indent=2))

    def __getstate__(self):
        state=self.__dict__.copy()
        net=state.pop('network_',None)
        if net is not None:state['_weights']={k:v.detach().cpu() for k,v in net.state_dict().items()}
        return state

    def __setstate__(self,state):
        weights=state.pop('_weights',None);self.__dict__.update(state)
        if weights is not None:
            self.device_='cpu';self.network_=self._network(len(self.feature_names_));self.network_.load_state_dict(weights);self.network_.eval()
