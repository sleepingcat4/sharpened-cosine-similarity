import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
import torch.nn.init as init


class SharpCosSim2d(nn.Conv2d):
    def __init__(
        self,
        channels_in: int,
        features: int,
        kernel_size: int=3,
        padding: int=0,
        stride: int=1,
        groups: int=1,
        shared_weights: bool = True,
        p_init: float=.7,
        q_init: float=1.,
        p_scale: float=5.,
        q_scale: float=.3,
        eps: float=1e-6,
    ):
        assert groups == 1 or groups == channels_in, " ".join([
            "'groups' needs to be 1 or 'channels_in' ",
            f"({channels_in})."])
        assert features % groups == 0, " ".join([
            "The number of",
            "output channels needs to be a multiple of the number",
            "of groups.\nHere there are",
            f"{features} output channels and {groups}",
            "groups."])

        self.channels_in = channels_in
        self.features = features
        self.stride = stride
        self.groups = groups
        self.shared_weights = shared_weights

        if self.groups == 1:
            self.shared_weights = False

        super(SharpCosSim2d, self).__init__(
            self.channels_in,
            self.features,
            kernel_size,
            bias=False,
            padding=padding,
            stride=stride,
            groups=self.groups)

        # Overwrite self.kernel_size created in the 'super' above.
        # We want an int, assuming a square kernel, rather than a tuple.
        self.kernel_size = kernel_size

        # Scaling weights in this way generates kernels that have
        # an l2-norm of about 1. Since they get normalized to 1 during
        # the forward pass anyway, this prevents any numerical
        # or gradient weirdness that might result from large amounts of
        # rescaling.
        self.channels_per_kernel = self.channels_in // self.groups
        weights_per_kernel = self.channels_per_kernel * self.kernel_size ** 2
        if self.shared_weights:
            self.n_kernels = self.features // self.groups
        else:
            self.n_kernels = self.features
        initialization_scale = (3 / weights_per_kernel) ** .5
        scaled_weight = np.random.uniform(
            low=-initialization_scale,
            high=initialization_scale,
            size=(
                self.n_kernels,
                self.channels_per_kernel,
                self.kernel_size,
                self.kernel_size)
        )
        self.weight = torch.nn.Parameter(torch.Tensor(scaled_weight))

        self.p_scale = p_scale
        self.q_scale = q_scale
        self.p = torch.nn.Parameter(
            torch.full((1, self.n_kernels, 1, 1), float(p_init * self.p_scale)))
        self.q = torch.nn.Parameter(
            torch.full((1, 1, 1, 1), float(q_init * self.q_scale)))
        self.eps = eps

    def forward(self, inp: torch.Tensor) -> torch.Tensor:
        # Scale and transform the p and q parameters
        # to ensure that their magnitudes are appropriate
        # and their gradients are smooth
        # so that they will be learned well.
        p = torch.exp(self.p / self.p_scale)
        q = torch.exp(-self.q / self.q_scale)

        # If necessary, expand out the weight and p parameters.
        if self.shared_weights:
            weight = torch.tile(self.weight, (self.groups, 1, 1, 1))
            p = torch.tile(p, (1, self.groups, 1, 1))
        else:
            weight = self.weight

        return self.scs(inp, weight, p, q)

    def scs(self, inp, weight, p, q):
        # Normalize the kernel weights.
        weight = weight / self.weight_norm(weight)

        # Normalize the inputs and
        # Calculate the dot product of the normalized kernels and the
        # normalized inputs.
        cos_sim = F.conv2d(
            inp,
            weight,
            stride=self.stride,
            padding=self.padding,
            groups=self.groups,
        ) / self.input_norm(inp, q)

        # Raise the result to the power p, keeping the sign of the original.
        return cos_sim.sign() * (cos_sim.abs() + self.eps) ** p

    def weight_norm(self, weight):
        # Find the l2-norm of the weights in each kernel.
        return weight.square().sum(dim=(1, 2, 3), keepdim=True).sqrt()

    def input_norm(self, inp, q):
        # Find the l2-norm of the inputs at each position of the kernels.
        # Sum the squared inputs over each set of kernel positions
        # by convolving them with the mock all-ones kernel weights.
        xnorm = F.conv2d(
            inp.square(),
            torch.ones((
                self.groups,
                self.channels_per_kernel,
                self.kernel_size,
                self.kernel_size)),
            stride=self.stride,
            padding=self.padding,
            groups=self.groups)

        # Add in the q parameter. 
        xnorm = (xnorm + self.eps).sqrt() + q
        outputs_per_group = self.features // self.groups
        return torch.repeat_interleave(xnorm, outputs_per_group, axis=1)


# Aliases for the class name
SharpCosSim2D = SharpCosSim2d
SharpCosSim = SharpCosSim2d
SCS = SharpCosSim2d
SharpenedCosineSimilarity = SharpCosSim2d
sharp_cos_sim = SharpCosSim2d