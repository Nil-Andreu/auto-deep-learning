"""
Things we take into consideration for creating the model:
- Complexity of the task: how many images and how many classes we want to infer
- Objective of the model: if a model if for production for a company, we might want to optimize tradeoff between throughtput and accuracy

The training of the model:
- Similarity with previous dataset: most of the models are trained with Imagenet and then we apply Transfer Learning. So depending on similarity, we make some warmup of the weights of the whole feature selection for some epochs or we only train the last layers.
- Type of the model: each model supported has the HP for which it was trained
"""
from typing import Optional

import torch
import numpy as np
from torchsummary import summary

from auto_deep_learning.enum import (
    ModelObjective,
    ModelName, 
    OptimizerType
)
from auto_deep_learning.utils import DatasetSampler
from auto_deep_learning.utils.config import ConfigurationObject
from auto_deep_learning.utils.model import get_criterion, get_optimizer, default_weight_init
from auto_deep_learning.utils.functions import to_cuda, count_model_parameters
from auto_deep_learning.model.definition import define_model
from auto_deep_learning.model.inference import inference

conf_obj = ConfigurationObject()


# TODO: Save also the whole model (both in gpu and cpu) and load the whole model (without predefinition of the arch)
class Model:
    def __init__(
        self,
        data: DatasetSampler,
        description: Optional[str] = '',
        objective: Optional[ModelObjective] = conf_obj.objective,
        model_name: Optional[ModelName] = '',
        model_version: Optional[str] = '',
        input_shape: Optional[int] = conf_obj.image_size,
    ):
        """Instance of the Neural Network model.

        Args:
            data (Loader): the loader of the data that will be used
            description (Optional[str], optional): short description of which task do you want to do. Defaults to None.
            objective (Optional[ModelObjective], optional): definition of which is the objective. Defaults to throughput, as are the simpler and optimized for production environments.
            model_name (Optional[ModelName], optional): definition of which is the model name (from HuggingFace). 
            model_version (Optional[str], optional): definition of which is the model version (from HuggingFace).
        """
        
        self.data = data
        self.objective = objective
        self.model_name = model_name
        self.description = description
        self.model_version = model_version
        self.input_shape = input_shape

        self.model = define_model(
            data=self.data,
            description=self.description,
            objective=self.objective,
            model_name=self.model_name,
            model_version=self.model_version,
            input_shape=self.input_shape # TODO: Adapt to different input shapes
        )

        self.criterion = get_criterion()


    @classmethod
    def fit(
        self,
        lr: Optional[int],  # TODO: Create function for default lr  -> 1e-4? Depends on self.model.recommended_lr, self.model.recommended_n_epochs
        n_epochs: Optional[int] = conf_obj.n_epochs,  # TODO: Create function for default lr 
        use_cuda: Optional[bool] = torch.cuda.is_available(),
        save_path: Optional[str] = 'model.pt',
    ):
        """Train of the model

        Args:
            lr (int): the learning rate of the optimizer
            n_epochs (int): number of epochs that we will train
            use_cuda (bool, optional): whether we train the model in cuda. Defaults to torch.cuda.is_available().
            save_path (str, optional): the path to save the best model. Defaults to 'model.pt'.

        Returns:
            _type_: _description_
            
        """

        # initialize tracker for minimum validation loss
        valid_loss_min = np.Inf 
        optimizer = get_optimizer(self.model, lr)

        if use_cuda:
            self.model.cuda()

        for epoch in range(1, n_epochs+1):
            # initialize variables to monitor training and validation loss
            train_loss, valid_loss = 0.0, 0.0
            
            # set the module to training mode
            self.model.train()

            for batch_idx, (data, target) in enumerate(self.data['train']):
                # move to GPU
                if use_cuda:
                    # TODO: Multiple targets and data (for now data is only img)
                    data, target = to_cuda(data), to_cuda(target)

                optimizer.zero_grad()
                # Obtain the output from the model
                output = self.model(data) # TODO: Multiple outputs, and each of them needs to be compared to the target
                
                # TODO: Check this works fine
                # Obtain loss for each of the targets we have
                for target_key in target.keys():
                    target_output = output[target_key]
                    target_expected = target[target_key]

                    if loss in locals():
                        loss += self.criterion(
                        target_output, 
                        target_expected
                    ) 

                    else:
                        loss = self.criterion(
                            target_output, 
                            target_expected
                        )

                # Backward induction
                loss.backward()
                # Perform optimization step
                optimizer.step()  

                train_loss = train_loss + ((1 / (batch_idx + 1)) * (loss.data.item() - train_loss))
                del loss

            # set the model to evaluation mode
            self.model.eval()

            if valid_loader := self.data.get('valid'):
                for batch_idx, (data, target) in enumerate(valid_loader):
                    # move to GPU
                    if use_cuda:
                        data, target = data.cuda(), target.cuda()

                    output = self.model(data)
                    # Obtain the loss
                    for target_key in target.keys():
                        target_output = output[target_key]
                        target_expected = target[target_key]

                        if loss in locals():
                            loss += self.criterion(
                            target_output, 
                            target_expected
                        ) 

                        else:
                            loss = self.criterion(
                                target_output, 
                                target_expected
                            )

                    # Add this loss to the list (same as before but instead of train we use valid)
                    valid_loss = valid_loss + ((1 / (batch_idx + 1)) * (loss.data.item() - valid_loss))
                    del loss

                # TODO: Use logger instead of prints
                # print training/validation statistics 
                print('Epoch: {} \tTraining Loss: {:.6f} \tValidation Loss: {:.6f}'.format(
                    epoch, 
                    train_loss,
                    valid_loss
                    ))

                if valid_loss < valid_loss_min:
                    # Print an alert
                    print('Validation loss decreased ({:.6f} --> {:.6f}).  Saving model..'.format(
                        valid_loss_min,
                        valid_loss))

                    torch.save(self.model.state_dict(), save_path)
                    
                    # Update the new minimum
                    valid_loss_min = valid_loss
            
        return self.model
    

    @classmethod
    def test(
        self,
        use_cuda: bool = torch.cuda.is_available()
    ):
        # monitor test loss and accuracy
        test_loss = 0.
        correct = 0.
        total = 0.

        # set the module to evaluation mode
        self.model.eval()

        # Move cuda after eval so consumes less memory
        if use_cuda:
            self.model.cuda()

        if loader_test := self.data.get('test'):
            for batch_idx, (data, target) in enumerate(loader_test):
                # move to GPU
                if use_cuda:
                    data, target = to_cuda(data), to_cuda(target)

                # forward pass: compute predicted outputs by passing inputs to the model
                output = self.model(data)

                # calculate the loss
                # TODO: Testing with multiple outputs & targets
                loss = self.criterion(output, target)

                # update average test loss 
                test_loss = test_loss + ((1 / (batch_idx + 1)) * (loss.data.item() - test_loss))

                # convert output probabilities to predicted class
                pred = output.data.max(1, keepdim=True)[1]

                # compare predictions to true label
                correct += np.sum(np.squeeze(pred.eq(target.data.view_as(pred))).cpu().numpy())

                total += data.size(0)
                    
        print('Test Loss: {:.6f}\n'.format(test_loss))

        print('\nTest Accuracy: %2d%% (%2d/%2d)' % (
            100. * correct / total, 
            correct, 
            total)
        )

    
    @classmethod
    def reset(
        self
    ):

        self.model = default_weight_init(self.model)


    @classmethod
    def predict(
        self,
        img_path: str = 'predict.img'
    ):
        output_inference = inference(
            self.model,
            img_path=img_path
        )

        return output_inference
    

    @classmethod
    def model_parameters(
        self
    ):
        # TODO: Use log instead
        print(summary(self.model, (self.input_shape)))


    @property
    def count_parameters(
        self
    ):
        return count_model_parameters(self.model)
