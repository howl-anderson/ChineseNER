# -*- coding:utf-8 -*-
import json
import math
import pickle as cPickle
import random
import sys

import numpy as np
import tensorflow as tf
from tensorflow.contrib import rnn
from tensorflow.contrib.crf import crf_log_likelihood, viterbi_decode
from tensorflow.contrib.layers.python.layers import initializers

from data_utils import DataBatch
from utils import f1_score, format_result, get_tags, new_f1_score


class Model():
    def __init__(self):
        self.nums_tags = 4
        self.lstm_dim = 50
        self.embedding_size = 50
        self.max_epoch = 10000
        self.global_steps = tf.Variable(0, trainable=False)
        self.best_dev_f1 = tf.Variable(0.0, trainable=False)
        self.checkpoint_dir = "./model/"
        self.checkpoint_path = "./model/ner.org.ckpt"

        self.initializer = initializers.xavier_initializer()

    def __creat_model(self):

        self._init_placeholder()
        # embbeding layer
        self.embedding_layer()

        # bi-Lstm layer
        self.biLSTM_layer()

        # logits_layer
        self.logits_layer()

        # loss_layer
        self.loss_layer()

        # optimizer_layer
        self.optimizer_layer()

    def _init_placeholder(self):

        self.inputs = tf.placeholder(
            dtype=tf.int32,
            shape=[None, None],
            name="Inputs"
        )

        self.targets = tf.placeholder(
            dtype=tf.int32,
            shape=[None, None],
            name="Targets"
        )

        self.dropout = tf.placeholder(
            dtype=tf.float32,
            shape=None,
            name="Dropout"
        )

        used = tf.sign(tf.abs(self.inputs))
        length = tf.reduce_sum(used, reduction_indices=1)
        self.length = tf.cast(length, tf.int32)
        self.batch_size = tf.shape(self.inputs)[0]
        self.nums_steps = tf.shape(self.inputs)[-1]

    def embedding_layer(self):
        with tf.variable_scope("embedding_layer") as scope:
            sqart3 = math.sqrt(3)

            self.embedding_matrix = tf.get_variable(
                name="embedding_matrix",
                shape=[self.input_size, self.embedding_size],
                initializer=self.initializer,
                dtype=tf.float32,
            )

            self.embedded = tf.nn.embedding_lookup(
                self.embedding_matrix, self.inputs
            )

            self.model_inputs = tf.nn.dropout(
                self.embedded, self.dropout
            )

    def biLSTM_layer(self):
        with tf.variable_scope("bi-LSTM") as scope:
            lstm_cell = {}
            for direction in ["forward", "backward"]:
                with tf.variable_scope(direction):
                    lstm_cell[direction] = rnn.GRUCell(
                        num_units=self.lstm_dim,
                        # use_peepholes=True,
                        # initializer=self.initializer,
                        # state_is_tuple=True
                    )

            outputs, final_states = tf.nn.bidirectional_dynamic_rnn(
                cell_fw=lstm_cell['forward'],
                cell_bw=lstm_cell['backward'],
                inputs=self.model_inputs,
                sequence_length=self.length,
                dtype=tf.float32,
            )
            self.lstm_outputs = tf.concat(outputs, axis=2)

    def logits_layer(self):
        with tf.variable_scope("hidden"):
            w = tf.get_variable("W", shape=[self.lstm_dim*2, self.lstm_dim],
                                dtype=tf.float32, initializer=self.initializer
                                )
            b = tf.get_variable("b", shape=[self.lstm_dim], dtype=tf.float32,
                                initializer=self.initializer
                                )

            output = tf.reshape(self.lstm_outputs, shape=[-1, self.lstm_dim*2])
            hidden = tf.tanh(tf.nn.xw_plus_b(output, w, b))
            self.hidden = hidden

        with tf.variable_scope("scope"):
            w = tf.get_variable("W", shape=[self.lstm_dim, self.nums_tags],
                                initializer=self.initializer, dtype=tf.float32
                                )
            self.test_w = w
            b = tf.get_variable("b", shape=[self.nums_tags], dtype=tf.float32)
            self.test_b = b
            pred = tf.nn.xw_plus_b(hidden, w, b)
            self.logits = tf.reshape(
                pred, shape=[-1, self.nums_steps, self.nums_tags])

    def loss_layer(self):
        with tf.variable_scope("loss_layer"):
            logits = self.logits
            targets = self.targets

            self.trans = tf.get_variable(
                "transitions",
                shape=[self.nums_tags, self.nums_tags],
                initializer=self.initializer
            )

            log_likelihood, self.trans = crf_log_likelihood(
                inputs=logits,
                tag_indices=targets,
                transition_params=self.trans,
                sequence_lengths=self.length
            )
            self.loss = tf.reduce_mean(-log_likelihood)

    def optimizer_layer(self):
        with tf.variable_scope("optimizer"):
            self.train_op = tf.train.AdamOptimizer().minimize(
                self.loss, global_step=self.global_steps)
        self.saver = tf.train.Saver(tf.global_variables(), max_to_keep=5)

        correct_prediction = tf.equal(
            tf.argmax(self.logits, 2), tf.cast(self.targets, tf.int64))
        self.accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32))

    def step(self, sess, batch):
        inputs, targets = zip(*batch)

        feed = {
            self.inputs: inputs,
            self.targets: targets,
            self.dropout: 0.5
        }
        global_steps, loss, _, logits, acc, length = sess.run(
            [self.global_steps, self.loss, self.train_op, self.logits, self.accuracy, self.length], feed_dict=feed)
        return global_steps, loss, logits, acc, length

    def train(self):
        self.train_data = DataBatch(data_type='train', batch_size=10)

        data = {
            "batch_size": self.train_data.batch_size,
            "input_size": self.train_data.input_size,
            "vocab": self.train_data.vocab,
            "tag_map": self.train_data.tag_map,
        }

        f = open("data/data_map.pkl", "wb")
        cPickle.dump(data, f)
        f.close()
        self.vocab = self.train_data.vocab
        self.input_size = len(self.vocab.values()) + 1
        self.nums_tags = len(self.train_data.tag_map.keys())
        self.tag_map = self.train_data.tag_map
        self.train_length = len(self.train_data.data)

        self.dev_data = DataBatch(data_type='dev', batch_size=300)
        self.dev_batch = self.dev_data.iteration()
        self.test_data = DataBatch(data_type='test', batch_size=100)
        self.test_batch = self.test_data.get_batch()
        # save vocab
        print("-"*50)
        print("train data:\t", self.train_length)
        print("input size:\t", self.input_size)
        print("nums of tags:\t", self.nums_tags)
        print("nums of vocab:\t", len(self.vocab.keys()))

        self.__creat_model()
        with tf.Session() as sess:
            ckpt = tf.train.get_checkpoint_state(self.checkpoint_dir)
            if ckpt and tf.train.checkpoint_exists(ckpt.model_checkpoint_path):
                print("restore model")
                self.saver.restore(sess, ckpt.model_checkpoint_path)
            else:
                sess.run(tf.global_variables_initializer())

            for i in range(self.max_epoch):
                print("-"*50)
                print("epoch {}".format(i))

                steps = 0
                for batch in self.train_data.get_batch():
                    steps += 1
                    global_steps, loss, logits, acc, length = self.step(
                        sess, batch)
                    if steps % 10 == 0:
                        print("[->] step {}/{}\tloss {:.2f}\tacc {:.2f}".format(
                            steps, len(self.train_data.batch_data), loss, acc))
                        self.evaluate(sess, "ORG")
                        self.evaluate(sess, "PER")

    def decode(self, scores, lengths, trans):
        paths = []
        for score, length in zip(scores, lengths):
            path, _ = viterbi_decode(score, trans)
            paths.append(path)
        return paths

    def evaluate(self, sess, tag):
        result = []
        trans = self.trans.eval()
        batch = self.dev_batch.__next__()
        inputs, targets = zip(*batch)
        feed = {
            self.inputs: inputs,
            self.targets: targets,
            self.dropout: 1
        }
        scores, acc, lengths = sess.run(
            [self.logits, self.accuracy, self.length], feed_dict=feed)

        pre_paths = self.decode(scores, lengths, trans)

        tar_paths = targets
        recall, precision, f1 = f1_score(
            tar_paths, pre_paths, tag, self.tag_map)
        # recall, precision, f1 = new_f1_score(
        #     tar_paths, pre_paths, tag, self.tag_map)
        best = self.best_dev_f1.eval()
        if f1 > best:
            print("\tnew best f1:")
            print("\trecall {:.2f}\t precision {:.2f}\t f1 {:.2f}".format(
                recall, precision, f1))
            tf.assign(self.best_dev_f1, f1).eval()
        self.saver.save(sess, self.checkpoint_path)

    def prepare_pred_data(self, text):
        vec = [self.vocab.get(i, 0) for i in text]
        feed = {
            self.inputs: [vec],
            self.dropout: 1
        }
        return feed

    def predict(self):
        f = open("data/data_map.pkl", "rb")
        maps = cPickle.load(f)
        f.close()
        self.vocab = maps.get("vocab", {})
        self.tag_map = maps.get("tag_map", {})
        self.nums_tags = len(self.tag_map.values())
        self.input_size = maps.get("input_size", 10000) + 1
        self.__creat_model()
        with tf.Session() as sess:
            ckpt = tf.train.get_checkpoint_state(self.checkpoint_dir)
            if ckpt and tf.train.checkpoint_exists(ckpt.model_checkpoint_path):
                print("[->] restore model")
                self.saver.restore(sess, ckpt.model_checkpoint_path)
            else:
                print("[->] no model, initializing")
                sess.run(tf.global_variables_initializer())

            trans = self.trans.eval()
            while True:
                text = input(" > ")
                feed = self.prepare_pred_data(text)

                logits, length = sess.run(
                    [self.logits, self.length], feed_dict=feed)
                paths = self.decode(logits, length, trans)
                org = get_tags(paths[0], "ORG", self.tag_map)
                org_entity = format_result(org, text, "ORG")
                per = get_tags(paths[0], "PER", self.tag_map)
                per_entity = format_result(per, text, "PER")

                resp = org_entity["entities"] + per_entity["entities"]
                print(json.dumps(resp, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("ChineseNer:\n1.train\t\tTraining the model\n2.predict\tTest the model")
        exit()
    model = Model()
    if sys.argv[1] == "train":
        model.train()
    elif sys.argv[1] == "predict":
        model.predict()
