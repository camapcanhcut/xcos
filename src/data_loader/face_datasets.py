import cv2
import os
import os.path as op
import warnings
from glob import glob
import numpy as np
import pandas as pd
from PIL import Image
import bcolz
import torch
import torch.nn as nn
from torchvision import transforms, datasets
from torch.utils.data import Dataset
from torch.utils.data.sampler import BatchSampler
from torchvision.datasets import ImageFolder
import random

from PIL import ImageFile

from utils.align import Alignment
from utils.util_python import read_lines_into_list

cos = nn.CosineSimilarity(dim=0, eps=1e-6)
# ImageFile is useless?
ImageFile.LOAD_TRUNCATED_IMAGES = True


class myImageFolder(ImageFolder):
    @property
    def train_labels(self):
        warnings.warn("train_labels has been renamed targets")
        return self.targets

    def __init__(self, root, transform=None, target_transform=None):
        super(myImageFolder, self).__init__(root, transform, target_transform)


class InsightFaceBinaryImg(Dataset):
    def __init__(self, root_folder, dataset_name, transform=None, mask_dir=None, use_bgr=True):
        self.root = root_folder
        self.name = dataset_name
        self.transform = transform
        print("self.root", self.root)
        print("self.name", self.name)
        print("self.transform", self.transform)
        self.img_arr, self.is_same_arr = self.get_val_pair(self.root, self.name)
        self.mask_dir = mask_dir
        self.use_bgr = use_bgr
        if self.mask_dir is not None:
            assert op.isdir(self.mask_dir)
            self.mask_files = glob(op.join(self.mask_dir, '*.png'))

    def __getitem__(self, index):
        img_pair = self.img_arr[index * 2: (index + 1) * 2]
        if not self.use_bgr:
            # Shape: from [2, c, h, w] to [2, h, w, c]
            img_pair = np.transpose(img_pair, (0, 2, 3, 1))
            # Range: [-1, +1] --> [0, 255]
            img_pair = ((img_pair + 1) * 0.5 * 255).astype(np.uint8)
        if self.mask_dir is not None:
            # Randomly choose one profile from the pair.
            mask_img_idx = np.random.choice(2)
            mask_file = np.random.choice(self.mask_files)
            img_pair[mask_img_idx] = self.apply_mask(img_pair[mask_img_idx], mask_file)

        # BGR2RGB
        img_pair_tmp = []
        for img in img_pair:
            if not self.use_bgr:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                if self.transform is not None:
                    img = self.transform(img)
                else:
                    raise NotImplementedError
            else:
                img = torch.tensor(img)
            img_pair_tmp.append(img)
        # img_pair = torch.stack(img_pair_tmp)
        is_same_label = self.is_same_arr[index]
        return {
            "data_input": (img_pair_tmp[0], img_pair_tmp[1]),
            "is_same_labels": is_same_label,
            "index": index
        }

    def __len__(self):
        return len(self.is_same_arr)

    def get_val_pair(self, path, name):
        print(op.join(path, name))
        # print(op.join(path, "{}_list.npy".format(name)))
        carray = bcolz.carray(rootdir=op.join(path, name), mode="r")
        # carray.flush()
        issame = np.load(op.join(path, "{}_list.npy".format(name)))
        return carray, issame

    def apply_mask(self, image, mask_path):
        """Apply the binary mask to one image.

        Arguments:
            image {np.array} -- of shape (h, w, c)
            mask_path {str} -- file path for one mask

        Returns:
            np.array -- masked image
        """
        mask = Image.open(mask_path)
        masked = np.array(image) * np.expand_dims(np.array(mask), 2)
        return masked


class SiameseDFWImageFolder(Dataset):
    """
    Train: For each sample creates randomly a positive or a negative pair
    Test: Creates fixed pairs for testing
    """

    def __init__(self, imgs_folder_dir, transform, dataset_type="training"):
        assert dataset_type in ["training", "testing"]
        print(">>> In SIFolder, imgfolderdir=", imgs_folder_dir)
        self.root = imgs_folder_dir
        self.dataset_type = dataset_type
        matrix_txt_path = os.path.join(
            self.root,
            "Mask_matrices",
            dataset_type,
            f"{dataset_type}_data_mask_matrix.txt",
        )
        self.data_mask_matrix = np.loadtxt(matrix_txt_path)
        img_path_list_path = os.path.join(
            self.root, f"{dataset_type.capitalize()}_data_face_name.txt"
        )
        self.img_path_list = read_lines_into_list(img_path_list_path)
        self.img_label_list, self.name2label = self.img_path_to_label_list(
            self.img_path_list
        )
        self.transform = transform
        # ############################################
        # self.wFace_dataset = ImageFolder(imgs_folder_dir, transform)
        self.class_num = len(self.name2label)
        # ##################################
        # # self.memoryAll = False

        # self.train_labels = np.array(self.wFace_dataset.targets, dtype=int)
        # print('>>> self.train_labels:', self.train_labels[1000:1010])

        # self.train_data = self.wFace_dataset

        # self.labels_set = set(self.train_labels)
        # self.label_to_indices = {label:
        #                          np.where(self.train_labels
        #                                   == label)[0]
        #                          for label in self.labels_set}
        # print('>>> Init SiameseDFWImageFolder done!')

    def __getitem__(self, idx):
        """
        img1 = (feat_fc, feat_grid)
        """
        # print('>>> In getItem, idx = ', idx)
        # Sample the 1-st image
        img1_path = os.path.join(self.root, self.img_path_list[idx])
        img1 = self.load_transformed_img_tensor(img1_path)
        label1 = self.img_label_list[idx]

        # Sample the 2-nd image
        # is_the_same_id is a bool that determines whether returning one pair with the same identity.
        is_the_same_id = np.random.randint(0, 2)
        ############
        img2_path = self.get_siamese_path(idx, is_the_same_id)
        img2_path = os.path.join(self.root, img2_path)
        # print("In getitem, img2_path: ", img2_path)
        # print("In getitem, img1_path: ", img1_path)
        img2 = self.load_transformed_img_tensor(img2_path)
        label2 = self.img_path_to_label(img2_path)
        ###################################
        # img1, label1 = self.train_data[index]  # , self.train_labels[index].item()
        # if target == 1:
        #     siamese_index = index
        #     while siamese_index == index:
        #         siamese_index = np.random.choice(self.label_to_indices[label1])
        # else:
        #     siamese_label = np.random.choice(
        #             list(self.labels_set - set([label1])))
        #     siamese_index = np.random.choice(
        #             self.label_to_indices[siamese_label])
        # img2, label2 = self.train_data[siamese_index]

        return img1, img2, label1, label2

    def __len__(self):
        return len(self.img_path_list)

    def img_path_to_label_list(self, path_list):
        label_list = []
        name_list = []
        name2label = {}
        for path in path_list:
            # path e.g. Training_data/Matthew_McConaughey/Matthew_McConaughey_h_002.jpg
            # Assume that Imposter Impersonator is one unique identity
            if "_I_" in path:
                name = path.split("/")[-1][:-8]
            else:
                name = path.split("/")[1]
            if name not in name_list:
                name_list.append(name)
                name2label[name] = len(name_list) - 1
            label = name2label[name]
            label_list.append(label)
        return label_list, name2label

    def img_path_to_label(self, path):
        # path e.g. data/dfw/Training_data/Matthew_McConaughey/Matthew_McConaughey_h_003.jpg
        if "_I_" in path:
            name = path.split("/")[-1][:-8]
        else:
            name = path.split("/")[3]

        return self.name2label[name]

    def load_transformed_img_tensor(self, path):
        img = datasets.folder.default_loader(path)
        # XXX
        t = transforms.Resize([112, 112])
        img = t(img)
        # print(img)
        # print('>>>>> In load_tr, img.size =', img.size())
        if self.transform is not None:
            img = self.transform(img)
        else:
            raise NotImplementedError
        return img

    def get_siamese_path(self, idx, is_the_same_id):
        """
        Input:
        """
        candidate = self.data_mask_matrix[idx]
        positions = []
        # print(">>>> Is the same", is_the_same_id)
        if is_the_same_id:
            targets = [1, 2]
            for target in targets:
                pos = np.where(candidate == target)[0]
                pos = list(pos)
                # print(">>>> candidate=", candidate)
                # print(">>>> pos= ", pos)
                positions += pos
            # _I.jpg case (no identical id)
            if len(positions) == 0:
                pos3 = np.where(candidate == 3)[0]
                pos3 = list(pos3)
                positions += pos3
        else:
            pos3 = np.where(candidate == 3)[0]
            pos4 = np.where(candidate == 4)[0]
            pos3 = list(pos3)
            pos4 = list(pos4)
            # print(">>>> candidate=", candidate)
            # print(">>>> pos3= ", pos3)
            # print(">>>> pos4= ", pos4)
            # _I.jpg case
            if len(pos4) > 0:
                pos4 = random.sample(pos4, max(len(pos3), 1))  # at least take 1 sample
                positions += pos4
            positions += pos3

        assert len(positions) > 0
        siamese_idx = random.choice(positions)
        return self.img_path_list[siamese_idx]


class SiameseImageFolder(Dataset):
    """
    Train: For each sample creates randomly a positive or a negative pair
    Test: Creates fixed pairs for testing
    """

    def __init__(self, imgs_folder_dir, transform):
        print(">>> In SIFolder, imgfolderdir=", imgs_folder_dir)
        self.root = imgs_folder_dir
        self.wFace_dataset = ImageFolder(imgs_folder_dir, transform)
        print("typeWFace", type(self.wFace_dataset))
        self.class_num = len(self.wFace_dataset.classes)
        print(">>> self.class_num = ", self.class_num)
        print("typeWFaceTarget", type(self.wFace_dataset.targets))
        print("typeWFaceTarget[last]", self.wFace_dataset.targets[-1])
        self.train_labels = np.array(self.wFace_dataset.targets, dtype=int)
        print("Type Train_lables", type(self.train_labels))
        print("Num Train_lables", len(self.train_labels))
        print(">>> self.train_labels:", self.train_labels[1000:1010])

        self.train_data = self.wFace_dataset

        self.labels_set = set(self.train_labels)
        self.label_to_indices = {
            label: np.where(self.train_labels == label)[0] for label in self.labels_set
        }
        print(">>> Init SiameseImageFolder done!")

    def __getitem__(self, index):
        """
        img1 = (feat_fc, feat_grid)
        """
        target = np.random.randint(0, 2)
        img1, label1 = self.train_data[index]  # , self.train_labels[index].item()
        if target == 1:
            siamese_index = index
            while siamese_index == index:
                siamese_index = np.random.choice(self.label_to_indices[label1])
        else:
            siamese_label = np.random.choice(list(self.labels_set - set([label1])))
            siamese_index = np.random.choice(self.label_to_indices[siamese_label])
        img2, label2 = self.train_data[siamese_index]

        return {"data_input": (img1, img2), "targeted_id_labels": (label1, label2)}

    def __len__(self):
        return len(self.wFace_dataset)


class SiameseWholeFace(Dataset):
    """
    Train: For each sample creates randomly a positive or a negative pair
    Test: Creates fixed pairs for testing
    """

    # @property
    # def train_data(self):
    #     warnings.warn("train_data has been renamed data")
    #     return self.wFace_dataset

    # @property
    # def test_data(self):
    #     warnings.warn("test_data has been renamed data")
    #     return self.wFace_dataset

    def __init__(self, wFace_dataset):
        self.wFace_dataset = wFace_dataset
        self.train = self.wFace_dataset.train
        self.memoryAll = self.wFace_dataset.memoryAll

        if self.train:
            self.train_labels = self.wFace_dataset.train_labels
            self.train_data = self.wFace_dataset
            if self.memoryAll:
                self.train_data = self.wFace_dataset.train_data

            self.labels_set = set(self.train_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.train_labels.numpy() == label)[0]
                for label in self.labels_set
            }
        else:
            # generate fixed pairs for testing
            # TODO: @property like MNIST
            self.test_labels = self.wFace_dataset.test_labels
            self.test_data = self.wFace_dataset
            if self.memoryAll:
                self.test_data = self.wFace_dataset.test_data
            self.labels_set = set(self.test_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.test_labels.numpy() == label)[0]
                for label in self.labels_set
            }

            random_state = np.random.RandomState(29)

            positive_pairs = [
                [
                    i,
                    random_state.choice(
                        self.label_to_indices[self.test_labels[i].item()]
                    ),
                    1,
                ]
                for i in range(0, len(self.test_data), 2)
            ]

            negative_pairs = [
                [
                    i,
                    random_state.choice(
                        self.label_to_indices[
                            np.random.choice(
                                list(
                                    self.labels_set - set([self.test_labels[i].item()])
                                )
                            )
                        ]
                    ),
                    0,
                ]
                for i in range(1, len(self.test_data), 2)
            ]
            self.test_pairs = positive_pairs + negative_pairs
        print(">>> Init SiameseWholeFace done!")

    def __getitem__(self, index):
        """
        img1 = (feat_fc, feat_grid)
        """
        if self.train:
            target = np.random.randint(0, 2)
            img1, label1 = self.train_data[index], self.train_labels[index].item()
            if target == 1:
                siamese_index = index
                while siamese_index == index:
                    siamese_index = np.random.choice(self.label_to_indices[label1])
            else:
                siamese_label = np.random.choice(list(self.labels_set - set([label1])))
                siamese_index = np.random.choice(self.label_to_indices[siamese_label])
            img2 = self.train_data[siamese_index]
        else:
            img1 = self.test_data[self.test_pairs[index][0]]
            img2 = self.test_data[self.test_pairs[index][1]]
            target = self.test_pairs[index][2]
        # [Depreciated] feat1 1 is of size [21504]
        # feat1, feat2 = img1.view(-1), img2.view(-1)
        # cosine = cos(feat1, feat2).numpy()
        # target = cosine
        feat_grid_1, feat_fc_1 = img1
        feat_grid_2, feat_fc_2 = img2
        return (feat_grid_1, feat_fc_1, feat_grid_2, feat_fc_2), target

    def __len__(self):
        return len(self.wFace_dataset)


class SiameseENM(Dataset):
    """
    Train: For each sample creates randomly a positive or a negative pair
    Test: Creates fixed pairs for testing
    """

    def __init__(self, ENM_dataset):
        self.ENM_dataset = ENM_dataset
        self.train = self.ENM_dataset.train
        # self.train = False

        if self.train:
            self.train_labels = self.ENM_dataset.train_labels
            self.train_data = self.ENM_dataset.train_data
            self.labels_set = set(self.train_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.train_labels.numpy() == label)[0]
                for label in self.labels_set
            }
        else:
            # generate fixed pairs for testing
            # TODO: @property like MNIST
            self.test_labels = self.ENM_dataset.test_labels
            self.test_data = self.ENM_dataset.test_data
            self.labels_set = set(self.test_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.test_labels.numpy() == label)[0]
                for label in self.labels_set
            }

            random_state = np.random.RandomState(29)

            positive_pairs = [
                [
                    i,
                    random_state.choice(
                        self.label_to_indices[self.test_labels[i].item()]
                    ),
                    1,
                ]
                for i in range(0, len(self.test_data), 2)
            ]

            negative_pairs = [
                [
                    i,
                    random_state.choice(
                        self.label_to_indices[
                            np.random.choice(
                                list(
                                    self.labels_set - set([self.test_labels[i].item()])
                                )
                            )
                        ]
                    ),
                    0,
                ]
                for i in range(1, len(self.test_data), 2)
            ]
            self.test_pairs = positive_pairs + negative_pairs

    def __getitem__(self, index):
        if self.train:
            target = np.random.randint(0, 2)
            img1, label1 = self.train_data[index], self.train_labels[index].item()
            if target == 1:
                siamese_index = index
                while siamese_index == index:
                    siamese_index = np.random.choice(self.label_to_indices[label1])
            else:
                siamese_label = np.random.choice(list(self.labels_set - set([label1])))
                siamese_index = np.random.choice(self.label_to_indices[siamese_label])
            img2 = self.train_data[siamese_index]
        else:
            img1 = self.test_data[self.test_pairs[index][0]]
            img2 = self.test_data[self.test_pairs[index][1]]
            target = self.test_pairs[index][2]

        return (img1, img2), target

    def __len__(self):
        return len(self.ENM_dataset)


class TripletENM(Dataset):
    """
    Train: For each sample (anchor) randomly chooses a positive and negative samples
    Test: Creates fixed triplets for testing
    """

    def __init__(self, ENM_dataset):
        self.ENM_dataset = ENM_dataset
        self.train = self.ENM_dataset.train

        if self.train:
            self.train_labels = self.ENM_dataset.train_labels
            self.train_data = self.ENM_dataset.train_data
            self.labels_set = set(self.train_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.train_labels.numpy() == label)[0]
                for label in self.labels_set
            }

        else:
            self.test_labels = self.ENM_dataset.test_labels
            self.test_data = self.ENM_dataset.test_data
            # generate fixed triplets for testing
            self.labels_set = set(self.test_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.test_labels.numpy() == label)[0]
                for label in self.labels_set
            }

            random_state = np.random.RandomState(29)

            triplets = [
                [
                    i,
                    random_state.choice(
                        self.label_to_indices[self.test_labels[i].item()]
                    ),
                    random_state.choice(
                        self.label_to_indices[
                            np.random.choice(
                                list(
                                    self.labels_set - set([self.test_labels[i].item()])
                                )
                            )
                        ]
                    ),
                ]
                for i in range(len(self.test_data))
            ]
            self.test_triplets = triplets

    def __getitem__(self, index):
        if self.train:
            img1, label1 = self.train_data[index], self.train_labels[index].item()
            positive_index = index
            while positive_index == index:
                positive_index = np.random.choice(self.label_to_indices[label1])
            negative_label = np.random.choice(list(self.labels_set - set([label1])))
            negative_index = np.random.choice(self.label_to_indices[negative_label])
            img2 = self.train_data[positive_index]
            img3 = self.train_data[negative_index]
        else:
            img1 = self.test_data[self.test_triplets[index][0]]
            img2 = self.test_data[self.test_triplets[index][1]]
            img3 = self.test_data[self.test_triplets[index][2]]

        return (img1, img2, img3), []

    def __len__(self):
        return len(self.ENM_dataset)


class SiameseMNIST(Dataset):
    """
    Train: For each sample creates randomly a positive or a negative pair
    Test: Creates fixed pairs for testing
    """

    def __init__(self, mnist_dataset):
        self.mnist_dataset = mnist_dataset

        self.train = self.mnist_dataset.train
        self.transform = self.mnist_dataset.transform

        if self.train:
            self.train_labels = self.mnist_dataset.train_labels
            self.train_data = self.mnist_dataset.train_data
            self.labels_set = set(self.train_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.train_labels.numpy() == label)[0]
                for label in self.labels_set
            }
        else:
            # generate fixed pairs for testing
            self.test_labels = self.mnist_dataset.test_labels
            self.test_data = self.mnist_dataset.test_data
            self.labels_set = set(self.test_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.test_labels.numpy() == label)[0]
                for label in self.labels_set
            }

            random_state = np.random.RandomState(29)

            positive_pairs = [
                [
                    i,
                    random_state.choice(
                        self.label_to_indices[self.test_labels[i].item()]
                    ),
                    1,
                ]
                for i in range(0, len(self.test_data), 2)
            ]

            negative_pairs = [
                [
                    i,
                    random_state.choice(
                        self.label_to_indices[
                            np.random.choice(
                                list(
                                    self.labels_set - set([self.test_labels[i].item()])
                                )
                            )
                        ]
                    ),
                    0,
                ]
                for i in range(1, len(self.test_data), 2)
            ]
            self.test_pairs = positive_pairs + negative_pairs

    def __getitem__(self, index):
        if self.train:
            target = np.random.randint(0, 2)
            img1, label1 = self.train_data[index], self.train_labels[index].item()
            if target == 1:
                siamese_index = index
                while siamese_index == index:
                    siamese_index = np.random.choice(self.label_to_indices[label1])
            else:
                siamese_label = np.random.choice(list(self.labels_set - set([label1])))
                siamese_index = np.random.choice(self.label_to_indices[siamese_label])
            img2 = self.train_data[siamese_index]
        else:
            img1 = self.test_data[self.test_pairs[index][0]]
            img2 = self.test_data[self.test_pairs[index][1]]
            target = self.test_pairs[index][2]

        img1 = Image.fromarray(img1.numpy(), mode="L")
        img2 = Image.fromarray(img2.numpy(), mode="L")
        if self.transform is not None:
            img1 = self.transform(img1)
            img2 = self.transform(img2)
        return (img1, img2), target

    def __len__(self):
        return len(self.mnist_dataset)


class TripletMNIST(Dataset):
    """
    Train: For each sample (anchor) randomly chooses a positive and negative samples
    Test: Creates fixed triplets for testing
    """

    def __init__(self, mnist_dataset):
        self.mnist_dataset = mnist_dataset
        self.train = self.mnist_dataset.train
        self.transform = self.mnist_dataset.transform

        if self.train:
            self.train_labels = self.mnist_dataset.train_labels
            self.train_data = self.mnist_dataset.train_data
            self.labels_set = set(self.train_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.train_labels.numpy() == label)[0]
                for label in self.labels_set
            }

        else:
            self.test_labels = self.mnist_dataset.test_labels
            self.test_data = self.mnist_dataset.test_data
            # generate fixed triplets for testing
            self.labels_set = set(self.test_labels.numpy())
            self.label_to_indices = {
                label: np.where(self.test_labels.numpy() == label)[0]
                for label in self.labels_set
            }

            random_state = np.random.RandomState(29)

            triplets = [
                [
                    i,
                    random_state.choice(
                        self.label_to_indices[self.test_labels[i].item()]
                    ),
                    random_state.choice(
                        self.label_to_indices[
                            np.random.choice(
                                list(
                                    self.labels_set - set([self.test_labels[i].item()])
                                )
                            )
                        ]
                    ),
                ]
                for i in range(len(self.test_data))
            ]
            self.test_triplets = triplets

    def __getitem__(self, index):
        if self.train:
            img1, label1 = self.train_data[index], self.train_labels[index].item()
            positive_index = index
            while positive_index == index:
                positive_index = np.random.choice(self.label_to_indices[label1])
            negative_label = np.random.choice(list(self.labels_set - set([label1])))
            negative_index = np.random.choice(self.label_to_indices[negative_label])
            img2 = self.train_data[positive_index]
            img3 = self.train_data[negative_index]
        else:
            img1 = self.test_data[self.test_triplets[index][0]]
            img2 = self.test_data[self.test_triplets[index][1]]
            img3 = self.test_data[self.test_triplets[index][2]]

        img1 = Image.fromarray(img1.numpy(), mode="L")
        img2 = Image.fromarray(img2.numpy(), mode="L")
        img3 = Image.fromarray(img3.numpy(), mode="L")
        if self.transform is not None:
            img1 = self.transform(img1)
            img2 = self.transform(img2)
            img3 = self.transform(img3)
        return (img1, img2, img3), []

    def __len__(self):
        return len(self.mnist_dataset)


class BalancedBatchSampler(BatchSampler):
    """
    BatchSampler - from a MNIST-like dataset, samples n_classes and within these classes samples n_samples.
    Returns batches of size n_classes * n_samples
    """

    def __init__(self, labels, n_classes, n_samples):
        self.labels = labels
        self.labels_set = list(set(self.labels.numpy()))
        self.label_to_indices = {
            label: np.where(self.labels.numpy() == label)[0]
            for label in self.labels_set
        }
        for l in self.labels_set:
            np.random.shuffle(self.label_to_indices[l])
        self.used_label_indices_count = {label: 0 for label in self.labels_set}
        self.count = 0
        self.n_classes = n_classes
        self.n_samples = n_samples
        self.n_dataset = len(self.labels)
        self.batch_size = self.n_samples * self.n_classes

    def __iter__(self):
        self.count = 0
        while self.count + self.batch_size < self.n_dataset:
            classes = np.random.choice(self.labels_set, self.n_classes, replace=False)
            indices = []
            for class_ in classes:
                indices.extend(
                    self.label_to_indices[class_][
                        self.used_label_indices_count[
                            class_
                        ]: self.used_label_indices_count[class_]
                        + self.n_samples
                    ]
                )
                self.used_label_indices_count[class_] += self.n_samples
                if self.used_label_indices_count[class_] + self.n_samples > len(
                    self.label_to_indices[class_]
                ):
                    np.random.shuffle(self.label_to_indices[class_])
                    self.used_label_indices_count[class_] = 0
            yield indices
            self.count += self.n_classes * self.n_samples

    def __len__(self):
        return self.n_dataset // self.batch_size


class IJBCVerificationBaseDataset(Dataset):
    """
        Base class of IJB-C verification dataset to read neccesary
        csv files and provide general functions.
    """

    def __init__(self, ijbc_data_root, leave_ratio=1.0):
        # read all csvs neccesary for verification
        self.ijbc_data_root = ijbc_data_root
        dtype_sid_tid = {"SUBJECT_ID": str, "TEMPLATE_ID": str}
        self.metadata = pd.read_csv(
            op.join(ijbc_data_root, "protocols", "ijbc_metadata_with_age.csv"),
            dtype=dtype_sid_tid,
        )
        test1_dir = op.join(ijbc_data_root, "protocols", "test1")
        self.enroll_templates = pd.read_csv(
            op.join(test1_dir, "enroll_templates.csv"), dtype=dtype_sid_tid
        )
        self.verif_templates = pd.read_csv(
            op.join(test1_dir, "verif_templates.csv"), dtype=dtype_sid_tid
        )
        self.match = pd.read_csv(op.join(test1_dir, "match.csv"), dtype=str)

        if leave_ratio < 1.0:  # shrink the number of verified pairs
            indice = np.arange(len(self.match))
            np.random.seed(0)
            np.random.shuffle(indice)
            left_number = int(len(self.match) * leave_ratio)
            self.match = self.match.iloc[indice[:left_number]]

    def _get_both_entries(self, idx):
        enroll_tid = self.match.iloc[idx]["ENROLL_TEMPLATE_ID"]
        verif_tid = self.match.iloc[idx]["VERIF_TEMPLATE_ID"]
        enroll_entries = self.enroll_templates[
            self.enroll_templates.TEMPLATE_ID == enroll_tid
        ]
        verif_entries = self.verif_templates[
            self.verif_templates.TEMPLATE_ID == verif_tid
        ]
        return enroll_entries, verif_entries

    def _get_cropped_path_suffix(self, entry):
        sid = entry["SUBJECT_ID"]
        filepath = entry["FILENAME"]
        img_or_frames, fname = op.split(filepath)
        fname_index, _ = op.splitext(fname)
        cropped_path_suffix = op.join(img_or_frames, f"{sid}_{fname_index}.jpg")
        return cropped_path_suffix

    def __len__(self):
        return len(self.match)


class IJBCVerificationDataset(IJBCVerificationBaseDataset):
    """
        IJB-C verification dataset (`test1` in the folder) who transforms
        the cropped faces into tensors.

        Note that entries in this verification dataset contains lots of
        repeated faces. A better way to evaluate a model's score is to
        precompute all faces features and store them into disks. (
        see `IJBCAllCroppedFacesDataset` and `IJBCVerificationPathDataset`)
    """

    def __init__(self, ijbc_data_root):
        super().__init__(ijbc_data_root)
        self.transforms = transforms.Compose(
            [
                transforms.Resize([112, 112]),
                transforms.ToTensor(),
                transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]),
            ]
        )

    def _get_cropped_face_image_by_entry(self, entry):
        cropped_path_suffix = self._get_cropped_path_suffix(entry)
        cropped_path = op.join(
            self.ijbc_data_root, "cropped_faces", cropped_path_suffix
        )
        return Image.open(cropped_path)

    def _get_tensor_by_entries(self, entries):
        faces_imgs = [
            self._get_cropped_face_image_by_entry(e) for idx, e in entries.iterrows()
        ]
        faces_tensors = [self.transforms(img) for img in faces_imgs]
        return torch.stack(faces_tensors, dim=0)

    def __getitem__(self, idx):
        enroll_entries, verif_entries = self._get_both_entries(idx)
        enroll_faces_tensor = self._get_tensor_by_entries(enroll_entries)
        verif_faces_tensor = self._get_tensor_by_entries(verif_entries)
        return {
            "enroll_faces_tensor": enroll_faces_tensor,
            "verif_faces_tensor": verif_faces_tensor,
        }


class IJBCVerificationPathDataset(IJBCVerificationBaseDataset):
    """
        This dataset read the match file of verification set in IJB-C
        (in the `test1` directory) and output the cropped faces' paths
        of both enroll_template and verif_template for each match.

        Models outside can use the path information to read their stored
        features and compute the similarity score of enroll_template and
        verif_template.
    """

    def __init__(self, ijbc_data_root, occlusion_lower_bound=0, leave_ratio=1.0):
        super().__init__(ijbc_data_root, leave_ratio=leave_ratio)
        self.occlusion_lower_bound = occlusion_lower_bound
        self.metadata["OCC_sum"] = self.metadata[[f"OCC{i}" for i in range(1, 19)]].sum(
            axis=1
        )
        self.reindexed_meta = self.metadata.set_index(["SUBJECT_ID", "FILENAME"])

    def _filter_out_occlusion_insufficient_entries(self, entries):
        if self.occlusion_lower_bound == 0:
            return [entry for _, entry in entries.iterrows()]

        out = []
        for _, entry in entries.iterrows():
            occlusion_sum = self.reindexed_meta.loc[
                (entry["SUBJECT_ID"], entry["FILENAME"]), "OCC_sum"
            ]
            if occlusion_sum.values[0] >= self.occlusion_lower_bound:
                out.append(entry)
        return out

    def __getitem__(self, idx):
        enroll_entries, verif_entries = self._get_both_entries(idx)

        is_same = (
            enroll_entries["SUBJECT_ID"].iloc[0] == verif_entries["SUBJECT_ID"].iloc[0]
        )
        is_same = 1 if is_same else 0
        enroll_template_id = (enroll_entries["TEMPLATE_ID"].iloc[0],)
        verif_template_id = (verif_entries["TEMPLATE_ID"].iloc[0],)

        enroll_entries = self._filter_out_occlusion_insufficient_entries(enroll_entries)
        verif_entries = self._filter_out_occlusion_insufficient_entries(verif_entries)

        def path_suffixes(entries):
            return [self._get_cropped_path_suffix(entry) for entry in entries]

        return {
            "enroll_template_id": enroll_template_id,
            "verif_template_id": verif_template_id,
            "enroll_path_suffixes": path_suffixes(enroll_entries),
            "verif_path_suffixes": path_suffixes(verif_entries),
            "is_same": is_same,
        }


class IJBVerificationPathDataset(Dataset):
    """
        This dataset read the match file of verification set in ijb_dataset_root
        (in the `meta` directory, the filename is sth. like
        "ijbc_template_pair_label.txt") and output the cropped faces'
        paths of both enroll_template and verif_template for each match.

        Models outside can use the path information to read their stored
        features and compute the similarity score of enroll_template and
        verif_template.
    """

    def __init__(self, ijb_dataset_root, leave_ratio=1.0, dataset_type="IJBB"):
        # TODO implement the leave_ratio method
        if dataset_type == "IJBB":
            match_filename = op.join(
                ijb_dataset_root, "meta", "ijbb_template_pair_label.txt"
            )
        elif dataset_type == "IJBC":
            match_filename = op.join(
                ijb_dataset_root, "meta", "ijbc_template_pair_label.txt"
            )
        else:
            raise NotImplementedError
        col_name = ["TEMPLATE_ID1", "TEMPLATE_ID2", "IS_SAME"]
        self.match = pd.read_csv(
            match_filename,
            delim_whitespace=True,
            header=None,
            dtype=str,
            names=col_name,
        )

        if leave_ratio < 1.0:  # shrink the number of verified pairs
            indice = np.arange(len(self.match))
            np.random.seed(0)
            np.random.shuffle(indice)
            left_number = int(len(self.match) * leave_ratio)
            self.match = self.match.iloc[indice[:left_number]]

    def __getitem__(self, idx):
        def path_suffixes(id_str):
            path = f"{id_str}.jpg"
            return [path]

        id1 = self.match.iloc[idx]["TEMPLATE_ID1"]
        id2 = self.match.iloc[idx]["TEMPLATE_ID2"]
        return {
            "enroll_template_id": id1,
            "verif_template_id": id2,
            "enroll_path_suffixes": path_suffixes(id1),
            "verif_path_suffixes": path_suffixes(id2),
            "is_same": self.match.iloc[idx]["IS_SAME"],
        }

    def __len__(self):
        return len(self.match)


class IJBCAllCroppedFacesDataset(Dataset):
    """
        This dataset loads all faces available in IJB-C and transform
        them into tensors. The path for that face is output along with
        its tensor.
        This is for models to compute all faces' features and store them
        into disks, otherwise the verification testing set contains too many
        repeated faces that should not be computed again and again.
    """

    def __init__(self, ijbc_data_root):
        self.ijbc_data_root = ijbc_data_root
        self.transforms = transforms.Compose(
            [
                transforms.Resize([112, 112]),
                transforms.ToTensor(),
                transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]),
            ]
        )
        self.all_cropped_paths_img = sorted(
            glob(op.join(self.ijbc_data_root, "cropped_faces", "img", "*.jpg"))
        )
        self.len_set1 = len(self.all_cropped_paths_img)
        self.all_cropped_paths_frames = sorted(
            glob(op.join(self.ijbc_data_root, "cropped_faces", "frames", "*.jpg"))
        )

    def __getitem__(self, idx):
        if idx < self.len_set1:
            path = self.all_cropped_paths_img[idx]
        else:
            path = self.all_cropped_paths_frames[idx - self.len_set1]
        img = Image.open(path).convert("RGB")
        tensor = self.transforms(img)
        return {
            "tensor": tensor,
            "path": path,
        }

    def __len__(self):
        return len(self.all_cropped_paths_frames) + len(self.all_cropped_paths_img)


class IJBCroppedFacesDataset(Dataset):
    """
        This dataset loads all faces available in IJB-B/C, align them,
        and transform them into tensors.
        The path for that face is output along with its tensor.
        This is for models to compute all faces' features and store them
        into disks, otherwise the verification testing set contains too many
        repeated faces that should not be computed again and again.
    """

    def __init__(self, ijbc_data_root, is_ijbb=True):
        self.ijbc_data_root = ijbc_data_root
        self.transforms = transforms.Compose(
            [
                transforms.Resize([112, 112]),
                transforms.ToTensor(),
                transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]),
            ]
        )
        self.img_dir = op.join(self.ijbc_data_root, "loose_crop")
        if is_ijbb:
            landmark_txt = "ijbb_name_5pts_score.txt"
        else:
            landmark_txt = "ijbc_name_5pts_score.txt"
        landmark_path = op.join(self.ijbc_data_root, "meta", landmark_txt)
        self.imgs_list, self.landmarks_list = self.loadImgPathAndLandmarks(
            landmark_path
        )
        self.alignment = Alignment()

    def loadImgPathAndLandmarks(self, path):
        imgs_list = []
        landmarks_list = []
        with open(path) as img_list:
            lines = img_list.readlines()
            for line in lines:
                name_lmk_score = line.strip().split(" ")
                img_name = os.path.join(self.img_dir, name_lmk_score[0])
                lmk = np.array(
                    [float(x) for x in name_lmk_score[1:-1]], dtype=np.float32
                )
                lmk = lmk.reshape((5, 2))

                imgs_list.append(img_name)
                landmarks_list.append(lmk)

        landmarks_list = np.array(landmarks_list)
        return imgs_list, landmarks_list

    def __getitem__(self, idx):
        img_path = self.imgs_list[idx]
        landmark = self.landmarks_list[idx]
        img = cv2.imread(img_path)
        # XXX cv2.cvtColor(img, cv2.COLOR_BGR2RGB) in the align function
        img = self.alignment.align(img, landmark)

        # img_feats.append(embedng.get(img,lmk))
        img = Image.fromarray(img)

        tensor = self.transforms(img)
        return {
            "tensor": tensor,
            "path": img_path,
        }

    def __len__(self):
        return len(self.imgs_list)


def make_square_box(box):
    width = box[2] - box[0]
    height = box[3] - box[1]
    if width > height:
        diff = width - height
        box[1] -= diff // 2
        box[3] += diff // 2
    elif height > width:
        diff = height - width
        box[0] -= diff // 2
        box[2] += diff // 2
    return box


class IJBAVerificationDataset(Dataset):
    def __init__(
        self,
        ijba_data_root="/tmp3/zhe2325138/IJB/IJB-A/",
        split_name="split1",
        only_first_image=False,
        aligned_facial_3points=False,
        crop_face=True,
    ):
        self.ijba_data_root = ijba_data_root
        split_root = op.join(ijba_data_root, "IJB-A_11_sets", split_name)
        self.only_first_image = only_first_image

        self.metadata = pd.read_csv(
            op.join(split_root, f"verify_metadata_{split_name[5:]}.csv")
        )
        self.metadata = self.metadata.set_index("TEMPLATE_ID")
        self.comparisons = pd.read_csv(
            op.join(split_root, f"verify_comparisons_{split_name[5:]}.csv"), header=None
        )

        self.transform = transforms.Compose(
            [
                transforms.Resize([112, 112]),
                transforms.ToTensor(),
                transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]),
            ]
        )

        self.aligned_facial_3points = aligned_facial_3points
        self.src_facial_3_points = self._get_source_facial_3points()
        self.crop_face = crop_face

    def _get_source_facial_3points(self, output_size=(112, 112)):
        # set source landmarks based on 96x112 size
        src = np.array(
            [
                [30.2946, 51.6963],  # left eye
                [65.5318, 51.5014],  # right eye
                [48.0252, 71.7366],  # nose
                # [33.5493, 92.3655],  # left mouth
                # [62.7299, 92.2041],  # right mouth
            ],
            dtype=np.float32,
        )

        # scale landmarkS to match output size
        src[:, 0] *= output_size[0] / 96
        src[:, 1] *= output_size[1] / 112
        return src

    def _get_face_img_from_entry(self, entry, square=True):
        fname = entry["FILE"]
        if fname[:5] == "frame":
            fname = "frames" + fname[5:]  # to fix error in annotation =_=
        img = Image.open(op.join(self.ijba_data_root, "images", fname)).convert("RGB")

        if self.aligned_facial_3points:
            raise NotImplementedError
        else:
            if self.crop_face:
                # left, upper, right, lower
                face_box = [
                    entry["FACE_X"],
                    entry["FACE_Y"],
                    entry["FACE_X"] + entry["FACE_WIDTH"],
                    entry["FACE_Y"] + entry["FACE_HEIGHT"],
                ]
                face_box = make_square_box(face_box) if square else face_box
                face_img = img.crop(face_box)
            else:
                face_img = img
        return face_img

    def _get_tensor_from_entries(self, entries):
        imgs = [self._get_face_img_from_entry(entry) for _, entry in entries.iterrows()]
        tensors = torch.stack([self.transform(img) for img in imgs])
        return tensors

    def __getitem__(self, idx):
        t1, t2 = self.comparisons.iloc[idx]
        t1_entries, t2_entries = self.metadata.loc[[t1]], self.metadata.loc[[t2]]
        if self.only_first_image:
            t1_entries, t2_entries = t1_entries.iloc[:1], t2_entries.iloc[:1]
        t1_tensors = self._get_tensor_from_entries(t1_entries)
        t2_tensors = self._get_tensor_from_entries(t2_entries)
        if self.only_first_image:
            t1_tensors, t2_tensors = t1_tensors.squeeze(0), t2_tensors.squeeze(0)

        s1, s2 = t1_entries["SUBJECT_ID"].iloc[0], t2_entries["SUBJECT_ID"].iloc[0]
        is_same = 1 if (s1 == s2) else 0
        return {
            "comparison_idx": idx,
            "t1_tensors": t1_tensors,
            "t2_tensors": t2_tensors,
            "is_same": is_same,
        }

    def __len__(self):
        return len(self.comparisons)


class ARVerificationAllPathDataset(Dataset):
    "/tmp3/biolin/datasets/face/ARFace/test2"

    def __init__(
        self, dataset_root="/tmp2/zhe2325138/dataset/ARFace/mtcnn_aligned_and_cropped/"
    ):
        self.dataset_root = dataset_root
        self.face_image_paths = sorted(glob(op.join(self.dataset_root, "*.png")))

        self.transforms = transforms.Compose(
            [
                transforms.Resize([112, 112]),
                transforms.ToTensor(),
                transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]),
            ]
        )

    def __getitem__(self, idx):
        fpath = self.face_image_paths[idx]
        fname, _ = op.splitext(op.basename(fpath))

        image = Image.open(fpath)
        image_tensor = self.transforms(image)
        return {"image_tensor": image_tensor, "fname": fname}

    def __len__(self):
        return len(self.face_image_paths)


class ARFaceDataset(Dataset):
    def __init__(self, root_folder, transform=None):
        self.root = root_folder
        self.transform = transform
        with os.scandir(root_folder) as it:
            self.img_arr = pd.DataFrame([[entry.name, int(entry.name.split('-')[1])]
                                         for entry in it if entry.name.endswith('.bmp')],
                                        columns=['image_id', 'person_id'])

    def __getitem__(self, index):
        target = np.random.randint(0, 2)  # 0: same person, 1: different person
        row1 = self.img_arr.iloc[index]
        if target == 0:
            row2 = self.img_arr[self.img_arr['person_id'] == row1['person_id']].sample().iloc[0]
        else:
            row2 = self.img_arr[self.img_arr['person_id'] != row1['person_id']].sample().iloc[0]

        img1 = Image.open(os.path.join(self.root, row1['image_id']))
        img2 = Image.open(os.path.join(self.root, row2['image_id']))
        return {
            'data_input': (self.transform(img1), self.transform(img2)),
            'is_same_labels': row1['person_id'] == row2['person_id'],
            'index': index
        }

    def __len__(self):
        return self.img_arr.shape[0]


class GeneGANDataset(Dataset):
    def __init__(self, root_folder, identity_txt, transform=None):
        self.root = root_folder
        self.img_arr = pd.read_csv(identity_txt, sep=' ', header=None)
        self.img_arr.columns = ['image_id', 'person_id']
        for name in self.img_arr['image_id']:
            if not os.path.exists(os.path.join(self.root, name)):
                raise FileNotFoundError(f'{os.path.join(self.root, name)} does not exists.')
        self.transform = transform

    def __getitem__(self, index):
        target = np.random.randint(0, 2)  # 0: same person, 1: different person
        row1 = self.img_arr.iloc[index]
        if target == 0:
            row2 = self.img_arr[self.img_arr['person_id'] == row1['person_id']].sample().iloc[0]
        else:
            row2 = self.img_arr[self.img_arr['person_id'] != row1['person_id']].sample().iloc[0]

        img1 = Image.open(os.path.join(self.root, row1['image_id']))
        img2 = Image.open(os.path.join(self.root, row2['image_id']))
        return {
            'data_input': (self.transform(img1), self.transform(img2)),
            'targeted_id_labels': (row1['person_id'], row2['person_id'])
        }

    def __len__(self):
        return self.img_arr.shape[0]
