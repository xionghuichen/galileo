# Created by xionghuichen at 2022/10/25
# Email: chenxh@lamda.nju.edu.cn
import tensorflow as tf
# from rllab.spaces import box

# import tensorflow_probability as tfp
import numpy as np
from gym.spaces import Box

from stable_baselines.common.policies import BasePolicy, nature_cnn, register_policy
# from SLBDAO.common.const import *
EPS = 1e-6  # Avoid NaN (prevents division by zero or log of zero)
# CAP the standard deviation of the actor
LOG_STD_MAX = 2
LOG_STD_MIN = -20
default_initializer = tf.compat.v1.keras.initializers.he_normal()
# tf.contrib.layers.xavier_initializer()


def gaussian_likelihood(input_, mu_, log_std):
    """
    Helper to computer log likelihood of a gaussian.
    Here we assume this is a Diagonal Gaussian.

    :param input_: (tf.Tensor)
    :param mu_: (tf.Tensor)
    :param log_std: (tf.Tensor)
    :return: (tf.Tensor)
    """
    pre_sum = -0.5 * (((input_ - mu_) / (tf.exp(log_std) + EPS)) ** 2 + 2 * log_std + np.log(2 * np.pi))
    return tf.reduce_sum(pre_sum, axis=1)


def gaussian_entropy(log_std):
    """
    Compute the entropy for a diagonal gaussian distribution.

    :param log_std: (tf.Tensor) Log of the standard deviation
    :return: (tf.Tensor)
    """
    return tf.reduce_sum(log_std + 0.5 * np.log(2.0 * np.pi * np.e), axis=-1)


def mlp(input_ph, layers, activ_fn=tf.nn.relu, layer_norm=False, initializer=None,
        name=''):
    """
    Create a multi-layer fully connected neural network.

    :param input_ph: (tf.placeholder)
    :param layers: ([int]) Network architecture
    :param activ_fn: (tf.function) Activation function
    :param layer_norm: (bool) Whether to apply layer normalization or not
    :param initializer:
    :return: (tf.Tensor)
    """
    if initializer is None:
        initializer = default_initializer
    with tf.variable_scope(name, reuse=False):
        output = input_ph
        for i, layer_size in enumerate(layers):
            output = tf.layers.dense(output, layer_size, name='fc' + str(i), kernel_initializer=initializer)
            if layer_norm:
                output = tf.contrib.layers.layer_norm(output, center=True, scale=True)
            output = activ_fn(output)
        return output


def clip_but_pass_gradient(input_, lower=-1., upper=1.):
    clip_up = tf.cast(input_ > upper, tf.float32)
    clip_low = tf.cast(input_ < lower, tf.float32)
    return input_ + tf.stop_gradient((upper - input_) * clip_up + (lower - input_) * clip_low)


def apply_squashing_func(mu_, pi_, logp_pi):
    """
    Squash the ouput of the gaussian distribution
    and account for that in the log probability
    The squashed mean is also returned for using
    deterministic actions.

    :param mu_: (tf.Tensor) Mean of the gaussian
    :param pi_: (tf.Tensor) Output of the policy before squashing
    :param logp_pi: (tf.Tensor) Log probability before squashing
    :return: ([tf.Tensor])
    """
    # Squash the output
    deterministic_policy = tf.tanh(mu_)
    policy = tf.tanh(pi_)
    # OpenAI Variation:
    # To avoid evil machine precision error, strictly clip 1-pi**2 to [0,1] range.
    # logp_pi -= tf.reduce_sum(tf.log(clip_but_pass_gradient(1 - policy ** 2, lower=0, upper=1) + EPS), axis=1)
    # Squash correction (from original implementation)
    logp_pi -= tf.reduce_sum(tf.log(1 - policy ** 2 + EPS), axis=1)
    return deterministic_policy, policy, logp_pi


class SACPolicy(BasePolicy):
    """
    Policy object that implements a SAC-like actor critic

    :param sess: (TensorFlow session) The current TensorFlow session
    :param ob_space: (Gym Space) The observation space of the environment
    :param ac_space: (Gym Space) The action space of the environment
    :param n_env: (int) The number of environments to run
    :param n_steps: (int) The number of steps to run for each environment
    :param n_batch: (int) The number of batch to run (n_envs * n_steps)
    :param reuse: (bool) If the policy is reusable or not
    :param scale: (bool) whether or not to scale the input
    """

    def __init__(self, sess, ob_space, ac_space, n_env=1, n_steps=1, n_batch=None, reuse=False, scale=False):
        super(SACPolicy, self).__init__(sess, ob_space, ac_space, n_env, n_steps, n_batch, reuse=reuse, scale=scale)
        assert isinstance(ac_space, Box), "Error: the action space must be of type gym.spaces.Box"
        assert (np.abs(ac_space.low) == ac_space.high).all(), "Error: the action space low and high must be symmetric"

        self.qf1 = None
        self.qf2 = None
        self.value_fn = None
        self.policy = None
        self.deterministic_policy = None
        self.act_mu = None
        self.std = None

    def make_actor(self, obs=None, reuse=False, scope="pi"):
        """
        Creates an actor object

        :param obs: (TensorFlow Tensor) The observation placeholder (can be None for default placeholder)
        :param reuse: (bool) whether or not to resue parameters
        :param scope: (str) the scope name of the actor
        :return: (TensorFlow Tensor) the output tensor
        """
        raise NotImplementedError

    def make_critics(self, obs=None, action=None, reuse=False,
                     scope="values_fn", create_vf=True, create_qf=True):
        """
        Creates the two Q-Values approximator along with the Value function

        :param obs: (TensorFlow Tensor) The observation placeholder (can be None for default placeholder)
        :param action: (TensorFlow Tensor) The action placeholder
        :param reuse: (bool) whether or not to resue parameters
        :param scope: (str) the scope name
        :param create_vf: (bool) Whether to create Value fn or not
        :param create_qf: (bool) Whether to create Q-Values fn or not
        :return: ([tf.Tensor]) Mean, action and log probability
        """
        raise NotImplementedError

    def step(self, obs, state=None, mask=None, deterministic=False):
        """
        Returns the policy for a single step

        :param obs: ([float] or [int]) The current observation of the environment
        :param state: ([float]) The last states (used in recurrent policies)
        :param mask: ([float]) The last masks (used in recurrent policies)
        :param deterministic: (bool) Whether or not to return deterministic actions.
        :return: ([float]) actions
        """
        raise NotImplementedError

    def proba_step(self, obs, state=None, mask=None):
        """
        Returns the action probability params (mean, std) for a single step

        :param obs: ([float] or [int]) The current observation of the environment
        :param state: ([float]) The last states (used in recurrent policies)
        :param mask: ([float]) The last masks (used in recurrent policies)
        :return: ([float], [float])
        """
        raise NotImplementedError



class FeedForwardPolicy(SACPolicy):
    """
    Policy object that implements a DDPG-like actor critic, using a feed forward neural network.

    :param sess: (TensorFlow session) The current TensorFlow session
    :param ob_space: (Gym Space) The observation space of the environment
    :param ac_space: (Gym Space) The action space of the environment
    :param n_env: (int) The number of environments to run
    :param n_steps: (int) The number of steps to run for each environment
    :param n_batch: (int) The number of batch to run (n_envs * n_steps)
    :param reuse: (bool) If the policy is reusable or not
    :param layers: ([int]) The size of the Neural network for the policy (if None, default to [64, 64])
    :param cnn_extractor: (function (TensorFlow Tensor, ``**kwargs``): (TensorFlow Tensor)) the CNN feature extraction
    :param feature_extraction: (str) The feature extraction type ("cnn" or "mlp")
    :param layer_norm: (bool) enable layer normalisation
    :param reg_weight: (float) Regularization loss weight for the policy parameters
    :param act_fun: (tf.func) the activation function to use in the neural network.
    :param kwargs: (dict) Extra keyword arguments for the nature CNN feature extraction
    """

    def __init__(self, sess, ob_space, ac_space,  n_env=1, n_steps=1, n_batch=None, reuse=False, layers=None,
                 cnn_extractor=nature_cnn, feature_extraction="cnn", reg_weight=0.0,
                 use_tfp_imp=True, concate_ac=False,
                 layer_norm=False, act_fun=tf.nn.relu, uniform_std=False, **kwargs):
        super(FeedForwardPolicy, self).__init__(sess, ob_space, ac_space, n_env, n_steps, n_batch,
                                                reuse=reuse, scale=(feature_extraction == "cnn"))

        self._kwargs_check(feature_extraction, kwargs)
        self.layer_norm = layer_norm
        self.feature_extraction = feature_extraction
        self.cnn_kwargs = kwargs
        self.cnn_extractor = cnn_extractor
        self.reuse = reuse
        if layers is None:
            layers = [64, 64]
        self.layers = layers
        self.reg_loss = None
        self.reg_weight = reg_weight
        self.entropy = None
        self.uniform_std = uniform_std
        self.use_tfp_imp = use_tfp_imp
        self.concate_ac = concate_ac
        assert len(layers) >= 1, "Error: must have at least one hidden layer for the policy."

        self.activ_fn = act_fun

    def make_actor(self, obs=None, reuse=False, scope="pi", *args, **kwargs):
        if obs is None:
            obs = self.processed_obs

        with tf.variable_scope(scope, reuse=reuse):
            if self.feature_extraction == "cnn":
                pi_h = self.cnn_extractor(obs, **self.cnn_kwargs)
            else:
                pi_h = tf.layers.flatten(obs)

            pi_h = mlp(pi_h, self.layers, self.activ_fn, layer_norm=self.layer_norm)

            self.act_mu = mu_ = tf.layers.dense(pi_h, self.ac_space.shape[0], kernel_initializer=default_initializer)
            # Important difference with SAC and other algo such as PPO:
            # the std depends on the state, so we cannot use stable_baselines.common.distribution
            log_std = tf.layers.dense(pi_h, self.ac_space.shape[0], kernel_initializer=default_initializer)
            if self.uniform_std:
                log_std *= 0
        # Regularize policy output (not used for now)
        reg_loss = self.reg_weight * 0.5 * tf.reduce_mean(log_std ** 2)
        reg_loss += self.reg_weight * 0.5 * tf.reduce_mean(mu_ ** 2)
        self.reg_loss = reg_loss

        # OpenAI Variation to cap the standard deviation
        # activation = tf.tanh # for log_std
        # log_std = LOG_STD_MIN + 0.5 * (LOG_STD_MAX - LOG_STD_MIN) * (log_std + 1)
        # Original Implementation
        log_std = tf.clip_by_value(log_std, LOG_STD_MIN, LOG_STD_MAX)

        self.std = std = tf.exp(log_std)

        if self.use_tfp_imp:
            ds = tf.contrib.distributions # tfp.distributions
            dist = ds.MultivariateNormalDiag(loc=self.act_mu, scale_diag=self.std)
            x_t = dist.sample()
            log_pi_t = dist.log_prob(x_t)
            # squashing
            deterministic_policy = tf.nn.tanh(self.act_mu)
            policy = tf.nn.tanh(x_t)
            logp_pi = log_pi_t - tf.reduce_sum(tf.log(1 - policy ** 2 + EPS), axis=1)
            self.entropy = dist.entropy() # gaussian_entropy(log_std)
        else:
            # Reparameterization trick
            pi_ = mu_ + tf.random_normal(tf.shape(mu_)) * std
            logp_pi = gaussian_likelihood(pi_, mu_, log_std)
            self.entropy = gaussian_entropy(log_std)
            # MISSING: reg params for log and mu
            # Apply squashing and account for it in the probabilty
            deterministic_policy, policy, logp_pi = apply_squashing_func(mu_, pi_, logp_pi)
        self.policy = policy
        self.deterministic_policy = deterministic_policy

        return deterministic_policy, policy, logp_pi

    def make_critics(self, obs=None, action=None, reuse=False, scope="values_fn",
                     create_vf=True, create_qf=True):
        if obs is None:
            obs = self.processed_obs

        with tf.variable_scope(scope, reuse=reuse):
            if self.feature_extraction == "cnn":
                critics_h = self.cnn_extractor(obs, **self.cnn_kwargs)
            else:
                critics_h = tf.layers.flatten(obs)

            if create_vf:
                # Value function
                with tf.variable_scope('vf', reuse=reuse):
                    vf_h = mlp(critics_h, self.layers, self.activ_fn, layer_norm=self.layer_norm)
                    value_fn = tf.layers.dense(vf_h, 1, name="vf", kernel_initializer=default_initializer)
                self.value_fn = value_fn

            if create_qf:
                # Concatenate preprocessed state and action
                if self.concate_ac:
                    qf_h = tf.concat([critics_h, action], axis=-1)
                # Double Q values to reduce overestimation
                with tf.variable_scope('qf1', reuse=reuse):
                    if not self.concate_ac:
                        emb_critics_h = mlp(critics_h, self.layers[0:1], self.activ_fn, layer_norm=self.layer_norm, name='emb_critic')
                        emb_acs_h = mlp(action, self.layers[0:1], self.activ_fn, layer_norm=self.layer_norm, name='emb_acs')
                        qf_h = emb_critics_h + emb_acs_h
                        qf1_h = mlp(qf_h, self.layers[1:], self.activ_fn, layer_norm=self.layer_norm)
                    else:
                        qf1_h = mlp(qf_h, self.layers, self.activ_fn, layer_norm=self.layer_norm)

                    qf1 = tf.layers.dense(qf1_h, 1, name="qf1", kernel_initializer=default_initializer)
                with tf.variable_scope('qf2', reuse=reuse):
                    if not self.concate_ac:
                        emb_critics_h = mlp(critics_h, self.layers[0:1], self.activ_fn, layer_norm=self.layer_norm, name='emb_critic')
                        emb_acs_h = mlp(action, self.layers[0:1], self.activ_fn, layer_norm=self.layer_norm, name='emb_acs')
                        qf_h = emb_critics_h + emb_acs_h
                        qf2_h = mlp(qf_h, self.layers[1:], self.activ_fn, layer_norm=self.layer_norm)
                    else:

                        qf2_h = mlp(qf_h, self.layers, self.activ_fn, layer_norm=self.layer_norm)
                    qf2 = tf.layers.dense(qf2_h, 1, name="qf2", kernel_initializer=default_initializer)

                self.qf1 = qf1
                self.qf2 = qf2

        return self.qf1, self.qf2, self.value_fn

    def step(self, obs, state=None, mask=None, deterministic=False, *args, **kwargs):
        if deterministic:
            mean, std = self.sess.run([self.deterministic_policy, self.std], {self.obs_ph: obs})
            return mean, std
        return self.sess.run(self.policy, {self.obs_ph: obs})

    def kl(self, other, obs):
        mean, std = self.sess.run([self.act_mu, self.std], {self.obs_ph: obs})
        other_mean, other_std = self.sess.run([other.act_mu, other.std], {other.obs_ph: obs})
        logstd, other_logstd = np.log(std), np.log(other_std)
        # tf.reduce_sum(other.logstd - self.logstd + (tf.square(self.std) + tf.square(self.mean - other.mean)) / (2.0 * tf.square(other.std)) - 0.5, axis=-1)
        return np.mean(other_logstd - logstd + (np.square(std) + np.square(mean - other_mean)) /
                             (2.0 * np.square(other_std)) - 0.5, axis=-1)

    def proba_step(self, obs, state=None, mask=None):
        return self.sess.run([self.act_mu, self.std], {self.obs_ph: obs})


class CnnPolicy(FeedForwardPolicy):
    """
    Policy object that implements actor critic, using a CNN (the nature CNN)

    :param sess: (TensorFlow session) The current TensorFlow session
    :param ob_space: (Gym Space) The observation space of the environment
    :param ac_space: (Gym Space) The action space of the environment
    :param n_env: (int) The number of environments to run
    :param n_steps: (int) The number of steps to run for each environment
    :param n_batch: (int) The number of batch to run (n_envs * n_steps)
    :param reuse: (bool) If the policy is reusable or not
    :param _kwargs: (dict) Extra keyword arguments for the nature CNN feature extraction
    """

    def __init__(self, sess, ob_space, ac_space, n_env=1, n_steps=1, n_batch=None, reuse=False, **_kwargs):
        super(CnnPolicy, self).__init__(sess, ob_space, ac_space, n_env, n_steps, n_batch, reuse,
                                        feature_extraction="cnn", **_kwargs)


class LnCnnPolicy(FeedForwardPolicy):
    """
    Policy object that implements actor critic, using a CNN (the nature CNN), with layer normalisation

    :param sess: (TensorFlow session) The current TensorFlow session
    :param ob_space: (Gym Space) The observation space of the environment
    :param ac_space: (Gym Space) The action space of the environment
    :param n_env: (int) The number of environments to run
    :param n_steps: (int) The number of steps to run for each environment
    :param n_batch: (int) The number of batch to run (n_envs * n_steps)
    :param reuse: (bool) If the policy is reusable or not
    :param _kwargs: (dict) Extra keyword arguments for the nature CNN feature extraction
    """

    def __init__(self, sess, ob_space, ac_space, n_env=1, n_steps=1, n_batch=None, reuse=False, **_kwargs):
        super(LnCnnPolicy, self).__init__(sess, ob_space, ac_space, n_env, n_steps, n_batch, reuse,
                                          feature_extraction="cnn", layer_norm=True, **_kwargs)


class MlpPolicy(FeedForwardPolicy):
    """
    Policy object that implements actor critic, using a MLP (2 layers of 64)

    :param sess: (TensorFlow session) The current TensorFlow session
    :param ob_space: (Gym Space) The observation space of the environment
    :param ac_space: (Gym Space) The action space of the environment
    :param n_env: (int) The number of environments to run
    :param n_steps: (int) The number of steps to run for each environment
    :param n_batch: (int) The number of batch to run (n_envs * n_steps)
    :param reuse: (bool) If the policy is reusable or not
    :param _kwargs: (dict) Extra keyword arguments for the nature CNN feature extraction
    """

    def __init__(self, sess, ob_space, ac_space, n_env=1, n_steps=1, n_batch=None, reuse=False, **_kwargs):
        super(MlpPolicy, self).__init__(sess, ob_space, ac_space, n_env, n_steps, n_batch, reuse,
                                        feature_extraction="mlp", **_kwargs)


class LnMlpPolicy(FeedForwardPolicy):
    """
    Policy object that implements actor critic, using a MLP (2 layers of 64), with layer normalisation

    :param sess: (TensorFlow session) The current TensorFlow session
    :param ob_space: (Gym Space) The observation space of the environment
    :param ac_space: (Gym Space) The action space of the environment
    :param n_env: (int) The number of environments to run
    :param n_steps: (int) The number of steps to run for each environment
    :param n_batch: (int) The number of batch to run (n_envs * n_steps)
    :param reuse: (bool) If the policy is reusable or not
    :param _kwargs: (dict) Extra keyword arguments for the nature CNN feature extraction
    """

    def __init__(self, sess, ob_space, ac_space, n_env=1, n_steps=1, n_batch=None, reuse=False, **_kwargs):
        super(LnMlpPolicy, self).__init__(sess, ob_space, ac_space, n_env, n_steps, n_batch, reuse,
                                          feature_extraction="mlp", layer_norm=True, **_kwargs)


register_policy("CnnPolicy", CnnPolicy)
register_policy("LnCnnPolicy", LnCnnPolicy)
register_policy("MlpPolicy", MlpPolicy)
register_policy("LnMlpPolicy", LnMlpPolicy)
