import os
import torch
import torch.nn as nn
import argparse
import random
import time
from datetime import datetime
import wandb

from configs import cfg
from datasets import *
from models import DeeplabV3plus
from utils import setup_logger, IoU, OverallAcc
import segmentation_models_pytorch as smp

def combine_cfg(config_dir=None):
    cfg_base = cfg.clone()
    if config_dir:
        cfg_base.merge_from_file(config_dir)
    return cfg_base 

def read_file(directory):
    l = []
    with open(directory, "r") as f:
        for line in f.readlines():
            l.append(line[:-1])
    return l

def train(cfg, logger):
    best_iou = 0
    logger.info("Begin the training process")
    wandb.login(key="051cf82cb4b7ccdf04b0d76bf7e1d4f4733e87f7")
    wandb.init(project= "drone_deploy", name= "ResNet", config= {"batch_size": cfg.SOLVER.BATCH_SIZE,
                                                 "max_iter": cfg.SOLVER.MAX_ITER,
                                                 "model": "UNet",
                                                 "device": cfg.MODEL.DEVICE})

    device = torch.device(cfg.MODEL.DEVICE)

    #model = DeeplabV3plus(cfg.MODEL.ATROUS, cfg.MODEL.NUM_CLASSES)
    model = smp.Unet(encoder_name="resnet50",encoder_weights="imagenet",in_channels=3,classes=6,)
    model.to(device)

    max_iter = cfg.SOLVER.MAX_ITER
    stop_iter = cfg.SOLVER.STOP_ITER

    optimizer = torch.optim.SGD([p for p in model.parameters() if p.requires_grad], 
    lr=cfg.SOLVER.LR, momentum=cfg.SOLVER.MOMENTUM, weight_decay=cfg.SOLVER.WEIGHT_DECAY)
    optimizer.zero_grad()

    output_dir = cfg.OUTPUT_DIR
    if not os.path.isdir(output_dir):
        os.mkdir(output_dir)

    iteration = 0

    train_list = read_file(cfg.DATASETS.TRAIN_LIST)
    valid_list = read_file(cfg.DATASETS.VALID_LIST)

    train_data = VOCDataset(cfg.DATASETS.IMGDIR, cfg.DATASETS.LBLDIR,
                            img_list=train_list,
                            transformation=Compose([
                            ToTensor(), 
                            Normalization(), 
                            RandomScale(cfg.INPUT.MULTI_SCALES), 
                            RandomCrop(cfg.INPUT.CROP_SIZE), 
                            RandomFlip(cfg.INPUT.FLIP_PROB)]))
    val_data = VOCDataset(cfg.DATASETS.IMGDIR, cfg.DATASETS.LBLDIR,img_list = valid_list, transformation=
                         Compose([ToTensor(), Normalization(), RandomCrop(cfg.INPUT.CROP_SIZE)]))

    logger.info("Number of train images: " + str(len(train_data)))
    logger.info("Number of validation images: " + str(len(val_data)))
    
    train_loader = torch.utils.data.DataLoader(
        train_data,
        batch_size=cfg.SOLVER.BATCH_SIZE,
        shuffle=True,
        num_workers=4,
        pin_memory=True,
        drop_last=True
    )
    val_loader = torch.utils.data.DataLoader(
        val_data,
        batch_size=cfg.SOLVER.BATCH_SIZE,
        shuffle=True,
        num_workers=4,
        pin_memory=True,
        drop_last=True
    )

    criterion = nn.CrossEntropyLoss(ignore_index=255)

    logger.info("Start training")
    model.train()
    end = time.time()

    while iteration < stop_iter:
        for i, (images, labels) in enumerate(train_loader):
            model.train()
            data_time = time.time() - end
            end = time.time()

            optimizer.param_groups[0]['lr'] = cfg.SOLVER.LR * (1 - iteration/max_iter)**cfg.SOLVER.POWER

            optimizer.zero_grad()
            images = images.to(device)
            labels = labels.to(device)

        
            preds = model(images)
            
            loss = criterion(preds, labels)
            loss.backward()

            optimizer.step()

            iteration += 1
            infor = {"lr":optimizer.param_groups[0]['lr'],
                    "loss": loss,
                    "time/iter": data_time,
                    "iteration": iteration}
            if iteration % 20 == 0:
                logger.info("Iter [%d/%d] Loss: %f Time/iter: %f" % (iteration, 
                cfg.SOLVER.STOP_ITER, loss, data_time))
            if iteration % 1000 == 0:
                logger.info("Validation mode")
                model.eval()
                intersections = torch.zeros(cfg.MODEL.NUM_CLASSES).to(device)
                unions = torch.zeros(cfg.MODEL.NUM_CLASSES).to(device)
                rights = 0
                totals = 0
                for imgs, lbls in val_loader:

                    imgs = imgs.to(device)
                    lbls = lbls.to(device)

                    with torch.no_grad():
                        preds = model(imgs)
                        preds = preds.argmax(dim=1)
                        intersection, union = IoU(preds, lbls, cfg.MODEL.NUM_CLASSES)
                        intersections += intersection
                        unions += union
                        right, total = OverallAcc(preds, lbls, cfg.MODEL.NUM_CLASSES)
                        rights += right
                        totals += total

                ious = intersections / unions
                mean_iou = torch.mean(ious).item()
                acc = rights / totals
                results = "\n" + "Overall acc: " + str(acc) + " Mean IoU: " + str(mean_iou) + "Learning rate: " + str(optimizer.param_groups[0]['lr']) + "\n"
                for i, iou in enumerate(ious):
                    results += "Class " + str(i) + " IoU: " + str(iou.item()) + "\n"
                infor.update({"acc": float(str(acc)), "iou":float(str(mean_iou)) })
                results = results[:-2]
                logger.info(results)
                wandb.log(infor)
                torch.save({"model_state_dict": model.state_dict(), 
                            "iteration": iteration,
                            }, os.path.join(output_dir, "current_model.pkl"))
                if mean_iou > best_iou:
                    best_iou = mean_iou
                    torch.save({"model_state_dict": model.state_dict(), 
                            "iteration": iteration,
                            }, os.path.join(output_dir, "best_model.pkl"))
                logger.info("Best iou so far: " + str(best_iou))
            if iteration == stop_iter:
                break
    return model 

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pytorch training")
    #parser.add_argument("--config", default="")
    #args = parser.parse_args()
    #cfg = combine_cfg(args.config)
    #print(cfg.OUTPUT_DIR)
    logger = setup_logger("UNet ", cfg.OUTPUT_DIR, str(datetime.now()) + ".log")
    model = train(cfg, logger)
