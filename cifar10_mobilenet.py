r"""An example of how to use Tensorboard .

In this case, this script will:
  * Fine-tune a pre-trained MobileNet on Cifar-10 dataset
  * Summary the training process on Tensorboard
  * Visualize t-SNE
"""
import os
import numpy as np
import pandas as pd

import tensorflow as tf
from scipy.misc import imsave
from tensorboard.plugins import projector
from kerasHook import ProgressBar


_CIFAR10_CLASSES = 10
_HEIGHT, _WIDTH, _DEPTH = 32, 32, 3
_CLASSES = ('plane', 'car', 'bird', 'cat', 'deer',
            'dog', 'frog', 'horse', 'ship', 'truck')

# Disable debugging info from TF
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'  # or any {'0', '1', '2'}
# tf.logging.set_verbosity(tf.logging.DEBUG)

def main():
  # ########################
  # Configure hyper-params
  # ########################
  batch_size = 1024

  training_steps  = 5000
  steps_per_epoch = 1000
  epochs_per_eval = 1
  training_epochs = int(training_steps // steps_per_epoch)

  num_cores = 8
  shuffle_buffer = 2048

  multi_gpu = True
  # ########################
  # Load CIFAR-10 dataset
  # ########################
  cifar10 = tf.keras.datasets.cifar10.load_data()

  # ########################
  # Define an Estimator
  # ########################
  if multi_gpu:
    model_fn = tf.contrib.estimator.replicate_model_fn(
        cifar10_model_fn,
        loss_reduction=tf.losses.Reduction.MEAN)
  else:
    model_fn = cifar10_model_fn

  classifier = tf.estimator.Estimator(
      model_fn=model_fn,
      model_dir='model',
      config=tf.estimator.RunConfig().replace(
        keep_checkpoint_max=2,
        save_checkpoints_steps=steps_per_epoch,
        save_summary_steps=200,),
      params={
        'learning_rate': 0.001,
        'optimizer': tf.train.AdamOptimizer,
        'weight_decay': 2e-4,
        'multi_gpu': multi_gpu})

  tensors_to_log = ['train_accuracy', 'cross_entropy', 'train_loss']
  # #########################
  # Training/Eval
  # #########################

  print("==========================")
  print("Starting a training cycle")
  print("Multi_GPU: %s" % multi_gpu)
  print("Batch_size %s" % batch_size)
  print("Total Steps %s" % training_steps)
  print("Training epochs %s" % training_epochs)
  print("Total steps before eval %s" % int(epochs_per_eval * steps_per_epoch))
  print("==========================")

  for _ in range(training_epochs // epochs_per_eval):
    classifier.train(
      input_fn=lambda: input_fn(
          tf.estimator.ModeKeys.TRAIN, cifar10[0], None, batch_size,
          cifar10_preprocess, shuffle_buffer, num_cores, multi_gpu),
      steps=epochs_per_eval * steps_per_epoch,
      hooks=[ProgressBar(training_epochs, steps_per_epoch, tensors_to_log)])

    print("\nStart evaluating...")
    eval_results = classifier.evaluate(
      input_fn=lambda: input_fn(
        tf.estimator.ModeKeys.EVAL, cifar10[1], 1, batch_size,
        cifar10_preprocess, None, num_cores, multi_gpu))
    print(eval_results)

  # ##################################
  # Visualize t-SNE
  # ###################################
  outdir = 'model/projector'
  # Randomly pick 50 samples from each class
  images, labels = get_samples(cifar10[0], logdir=outdir,
                               samples_per_class=100)

  visualize_embeddings(images, classifier, outdir)

# #############################################################################
# CIFAR-10 PROCESSING
# #############################################################################
def cifar10_preprocess(image, label, mode):
  image = tf.image.per_image_standardization(image)
  image = tf.image.resize_image_with_crop_or_pad(image, _HEIGHT, _WIDTH)

  if mode == tf.estimator.ModeKeys.EVAL or \
      mode == tf.estimator.ModeKeys.TRAIN:

    label = tf.one_hot(tf.cast(label[0], tf.int32), _CIFAR10_CLASSES)
    if tf.estimator.ModeKeys.TRAIN:  # perform augmentation
      image = tf.random_crop(image, [_HEIGHT, _WIDTH, _DEPTH])
      image = tf.image.random_flip_left_right(image)
    return image, label

  else:  # predict mode
    return image


# #############################################################################
# INPUT_FN
# #############################################################################
def input_fn(mode, dataset, num_epochs, batch_size,
             preprocess_fn, shuffle_buffer=None,
             num_parallel_calls=4, multi_gpu=False):
  """

  Args:
    mode:
    dataset:
    num_epochs:
    batch_size:
    preprocess_fn:
    shuffle_buffer:
    num_parallel_calls:
    multi_gpu:

  Returns:

  """
  dataset = tf.data.Dataset.from_tensor_slices(dataset)
  dataset = dataset.prefetch(buffer_size=batch_size)

  if mode == tf.estimator.ModeKeys.TRAIN:
    dataset = dataset.shuffle(buffer_size=shuffle_buffer)

  dataset = dataset.repeat(num_epochs)
  if mode != tf.estimator.ModeKeys.PREDICT:
    dataset = dataset.map(
        lambda image, label: preprocess_fn(image, label, mode),
        num_parallel_calls=num_parallel_calls)
  else:
    dataset = dataset.map(
        lambda image: preprocess_fn(image, None, mode), num_parallel_calls)

  dataset = dataset.batch(batch_size)
  # Operations between the final prefetch and the get_next call to the iterator
  # will happen synchronously during run time. We prefetch here again to
  # background all of the above processing work and keep it out of the
  # critical training path.
  dataset = dataset.prefetch(1)

  return dataset


# #############################################################################
# MODEL_FN CONSTRUCTION
# #############################################################################
def cifar10_model_fn(features, labels, mode, params):
  """

  Args:
    features:
    labels:
    mode:
    params:

  Returns:

  """

  if mode == tf.estimator.ModeKeys.TRAIN:
    tf.keras.backend.set_learning_phase(True)
  else:
    tf.keras.backend.set_learning_phase(False)

  model = tf.keras.applications.MobileNet(
      input_tensor=features,
      input_shape=(32, 32, 3),
      include_top=False,
      pooling='avg',
      weights=None)
  feature_map = model(features)
  feature_map = tf.keras.layers.Dropout(0.7)(feature_map)
  logits = tf.keras.layers.Dense(units=_CIFAR10_CLASSES)(feature_map)

  predictions = {
      'classes': tf.argmax(logits, axis=1),
      'probabilities': tf.nn.softmax(logits, name='softmax_tensor'),
      'embeddings': feature_map
  }

  if mode == tf.estimator.ModeKeys.PREDICT:
    return tf.estimator.EstimatorSpec(
        mode=mode,
        predictions=predictions)

  # L2 Loss with weight decay
  cross_entropy = tf.losses.softmax_cross_entropy(
      onehot_labels=labels, logits=logits)
  loss = cross_entropy + params['weight_decay'] * \
         tf.add_n([tf.nn.l2_loss(t) for t in tf.trainable_variables()])

  # Setting up metrics
  accuracy = tf.metrics.accuracy(
      tf.argmax(labels, axis=1), predictions['classes'])
  metrics = {'accuracy': accuracy}

  if mode == tf.estimator.ModeKeys.TRAIN:
    global_step = tf.train.get_or_create_global_step()
    optimizer = params['optimizer'](params['learning_rate'])

    if params['multi_gpu']:
      optimizer = tf.contrib.estimator.TowerOptimizer(optimizer)

    # update ops for Batch Norm
    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
    with tf.control_dependencies(update_ops):
      train_op = optimizer.minimize(loss, global_step)
  else:
    train_op = None

  # Create a tensor named train_accuracy for logging purposes
  tf.identity(accuracy[1], name='train_accuracy')
  tf.identity(loss, name='train_loss')
  tf.identity(cross_entropy, name='cross_entropy')
  tf.summary.scalar('train_accuracy', accuracy[1])
  tf.summary.scalar('train_loss', loss)
  tf.summary.scalar('cross_entropy', cross_entropy)

  return tf.estimator.EstimatorSpec(
      mode=mode,
      predictions=predictions,
      loss=loss,
      train_op=train_op,
      eval_metric_ops=metrics)

# #############################################################################
# VISUALIZE T-SNE
# #############################################################################

def visualize_embeddings(images, estimator, outdir):

  summary_writer = tf.summary.FileWriter(outdir)

  # Extract embeddings (layer before output)
  predictions = estimator.predict(
    input_fn=lambda: input_fn(
      tf.estimator.ModeKeys.PREDICT, images, 1, 128,
      cifar10_preprocess, None, 8, True),
    predict_keys=['embeddings'])

  embeddings = np.stack([pred['embeddings'] for pred in predictions], axis=0)
  embedding_var = tf.Variable(embeddings, name='embeddings')

  with tf.Session() as sess:
    sess.run(embedding_var.initializer)
    config = projector.ProjectorConfig()

    embedding = config.embeddings.add()
    embedding.tensor_name = embedding_var.name
    embedding.metadata_path = os.path.abspath(os.path.join(outdir, 'metadata.csv'))
    embedding.sprite.image_path = os.path.abspath(os.path.join(outdir, 'sprite.png'))
    embedding.sprite.single_image_dim.extend([32, 32])

    projector.visualize_embeddings(summary_writer, config)
    saver = tf.train.Saver([embedding_var])

    saver.save(sess, os.path.join(outdir, 'embeddings.ckpt'), 1)

  sprite = images_to_sprite(images)
  imsave(os.path.join(outdir, 'sprite.png'), sprite)

def get_samples(data, samples_per_class, logdir):
  images, labels = data

  df = pd.DataFrame(labels, columns=['labels']).groupby('labels')
  samples = []
  meta_file = open(os.path.join(logdir, 'metadata.csv'), 'w')
  for cls in df.groups:
    samples_per_list = list(df.get_group(cls).sample(samples_per_class).index.values)
    samples.append(samples_per_list)
    for s in samples_per_list:
      meta_file.write('{},{}\n'.format(s, _CLASSES[cls]))

  meta_file.close()
  # flatten list
  samples_idx = [item for sublist in samples for item in sublist]
  return images[samples_idx], labels[samples_idx]


def images_to_sprite(data):
  """Creates the sprite image along with any necessary padding.
  Taken from: https://github.com/tensorflow/tensorflow/issues/6322
  Args:
    data: NxHxW[x3] tensor containing the images.
  Returns:
    data: Properly shaped HxWx3 image with any necessary padding.
  """
  if len(data.shape) == 3:
    data = np.tile(data[..., np.newaxis], (1, 1, 1, 3))
  data = data.astype(np.uint8)
  n = int(np.ceil(np.sqrt(data.shape[0])))
  padding = ((0, n ** 2 - data.shape[0]), (0, 0),
             (0, 0)) + ((0, 0),) * (data.ndim - 3)
  data = np.pad(data, padding, mode='constant',
                constant_values=0)
  # Tile the individual thumbnails into an image.
  data = data.reshape((n, n) + data.shape[1:]).transpose(
      (0, 2, 1, 3) + tuple(range(4, data.ndim + 1)))
  data = data.reshape((n * data.shape[1], n * data.shape[3]) + data.shape[4:])
  return data


if __name__ == '__main__':
    main()