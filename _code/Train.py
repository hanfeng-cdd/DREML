import numpy as np
import os, time, copy, random
from glob import glob

from torchvision import models, transforms, datasets
import torch.optim as optim
import torch.nn as nn
import torch
from .Loss import ProxyStatic
from .Reader import ImageReader

PHASE = ['tra','val']

class learn():
    def __init__(self,ID, no, idx, dst, gpuid, RGBmean, RGBstdv, data_dict, num_epochs=10, init_lr=0.01, decay=0.01, batch_size=400, imgsize=128, avg=8, num_workers=16):
        self.ID = ID
        self.no = no
        self.idx = idx
        self.dst = dst
        self.gpuid = gpuid
            
        self.batch_size = batch_size; print('batch size: {}'.format(batch_size))
        self.num_workers = num_workers; print('num workers: {}'.format(num_workers))
        
        self.decay_time = [False,False]
        self.init_lr = init_lr; print('init_lr : {}'.format(init_lr))
        self.decay_rate = decay
        self.num_epochs = num_epochs

        self.avg = avg
        self.data_dict_ori = data_dict
        
        self.imgsize = imgsize; print('image size: {}'.format(imgsize))
        self.RGBmean = RGBmean
        self.RGBstdv = RGBstdv
        
        self.record = []
        if not self.setsys(): print('system error'); return

    def run(self):
        self.loadData()
        self.setModel()
        self.criterion = ProxyStatic(self.classSize, self.classSize)############
        self.opt(self.num_epochs)
        return
    

    ##################################################
    # step 0: System check
    ##################################################
    def setsys(self):
        if not torch.cuda.is_available(): print('No GPU detected'); return False
        if not os.path.exists(self.dst): os.makedirs(self.dst)
        self.device = torch.device('cuda:0')
        return True
    
    ##################################################
    # step 1: Loading Data
    ##################################################
    def loadData(self):
        # balance data for each class
        TH = 200
        
        # sort classes and fix the class order  
        all_class = sorted([k for k in self.data_dict_ori], key = lambda x:x)

        # append image
        self.data_dict_meta = {i:[] for i in range(self.ID.max().item()+1)}
        for i in range(len(all_class)):
            meta_class = self.ID[i].item()
            tra_imgs = self.data_dict_ori[all_class[i]]
            if len(tra_imgs)>TH: tra_imgs = random.sample(tra_imgs,TH)
            self.data_dict_meta[meta_class]+=tra_imgs
        
        self.data_transforms = transforms.Compose([transforms.Resize(int(self.imgsize*1.1)),
                                                   transforms.RandomRotation(10),
                                                   transforms.RandomCrop(self.imgsize),
                                                   transforms.RandomHorizontalFlip(),
                                                   transforms.ToTensor(),
                                                   transforms.Normalize(self.RGBmean, self.RGBstdv)])
        

        self.dsets = ImageReader(self.data_dict_meta, self.data_transforms)
        print(len(self.dsets))
        self.classSize = len(self.data_dict_meta)
        print('output size: {}'.format(self.classSize))

        return
    
    ##################################################
    # step 2: Set Model
    ##################################################
    def setModel(self):
        print('Setting model')
        self.model = models.resnet18(pretrained=True)
        self.model.avgpool=nn.AvgPool2d(self.avg)
        num_ftrs = self.model.fc.in_features
        self.model.fc = nn.Linear(num_ftrs, self.classSize)
        
        print('Training on Single-GPU')
        self.model = self.model.to('cuda:0')
        self.optimizer = optim.SGD(self.model.parameters(), lr=self.init_lr, momentum=0.9)
        return
    
    def lr_scheduler(self, epoch):
        if epoch>=0.5*self.num_epochs and not self.decay_time[0]: 
            self.decay_time[0] = True
            lr = self.init_lr*self.decay_rate
            print('LR is set to {}'.format(lr))
            for param_group in self.optimizer.param_groups: param_group['lr'] = lr
        if epoch>=0.8*self.num_epochs and not self.decay_time[1]: 
            self.decay_time[1] = True
            lr = self.init_lr*self.decay_rate*self.decay_rate
            print('LR is set to {}'.format(lr))
            for param_group in self.optimizer.param_groups: param_group['lr'] = lr
        return
            
    ##################################################
    # step 3: Learning
    ##################################################
    def tra(self):
        # Set model to training mode
        self.model.train(True)
            
        dataLoader = torch.utils.data.DataLoader(self.dsets, batch_size=self.batch_size, shuffle=True, num_workers=self.num_workers)
        
        L_data, T_data, N_data = 0.0, 0, 0
        
        # iterate batch
        for data in dataLoader:
            self.optimizer.zero_grad()
            
            with torch.set_grad_enabled(True):
                inputs_bt, labels_bt = data # <FloatTensor> <LongTensor>
                fvec = self.model(inputs_bt.to('cuda:0'))
                loss = self.criterion(fvec, labels_bt)#.to('cuda:0')

                loss.backward()
                self.optimizer.step()  

            _, preds_bt = torch.max(fvec.to('cpu'), 1)

            L_data += loss.item()
            T_data += torch.sum(preds_bt == labels_bt).item()
            N_data += len(labels_bt)
            
        return L_data/N_data, T_data/N_data 
        
    def opt(self, num_epochs):
        # recording time and epoch acc and best result
        since = time.time()
        self.best_epoch = 0
        self.best_acc = 0
        for epoch in range(num_epochs):
            print('Epoch {}/{} \n '.format(epoch, num_epochs - 1) + '-' * 40)
            self.lr_scheduler(epoch)
            
            tra_loss, tra_acc = self.tra()
            
            self.record.append((epoch, tra_loss, tra_acc))
            print('tra - Loss:{:.4f} - Acc:{:.4f}'.format(tra_loss, tra_acc))
            
            # deep copy the model
            if epoch >= 1 and tra_acc> self.best_acc:
                self.best_acc = tra_acc
                self.best_epoch = epoch
                torch.save(self.model, self.dst + 'model_{:02}_{}.pth'.format(self.idx,self.no))
        
        torch.save(torch.Tensor(self.record), self.dst + 'record_{:02}_{}.pth'.format(self.idx, self.no))
        time_elapsed = time.time() - since
        print('Training complete in {:.0f}m {:.0f}s'.format(time_elapsed//60, time_elapsed%60))
        print('Best tra acc: {}'.format(self.best_acc))
        print('Best tra acc in epoch: {}'.format(self.best_epoch))
        return
    
    

    